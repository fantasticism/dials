"""
Module of utility functions for scaling.
"""

from __future__ import print_function
import logging
from time import time
import copy
from math import pi, acos
from dials.array_family import flex
from cctbx import miller, crystal
from dxtbx.model.experiment_list import ExperimentListDumper, ExperimentList
from dials_scaling_ext import create_sph_harm_table, calc_theta_phi,\
  rotate_vectors_about_axis

logger = logging.getLogger('dials')

def save_experiments(experiments, filename):
  """Save the experiments json."""
  st = time()
  logger.info('Saving the experiments to %s', filename)
  dump = ExperimentListDumper(experiments)
  with open(filename, "w") as outfile:
    outfile.write(dump.as_json(split=True))
  logger.info('Time taken: %g', (time() - st))

def save_reflections(scaler, filename):
  """Save the scaled reflections."""
  scaler.clean_reflection_table() #Remove unwanted columns.
  st = time()
  logger.info('Saving the scaled reflections to %s', filename)
  scaler.reflection_table.as_pickle(filename)
  logger.info('Time taken: %g', (time() - st))

def parse_multiple_datasets(reflections):
  """Parse multiple datasets from single reflection tables, selecting on id."""
  single_reflection_tables = []
  dataset_id_list = []
  for refl_table in reflections:
    dataset_ids = set(refl_table['id']).difference(set([-1]))
    n_datasets = len(dataset_ids)
    dataset_id_list.extend(list(dataset_ids))
    if n_datasets > 1:
      logger.info('Detected existence of a multi-dataset reflection table \n'
        'containing %s datasets. \n', n_datasets)
      for dataset_id in dataset_ids:
        single_refl_table = refl_table.select(refl_table['id'] == dataset_id)
        single_reflection_tables.append(single_refl_table)
    else:
      single_reflection_tables.append(refl_table)
    if len(dataset_id_list) != len(set(dataset_id_list)):
      logger.warn('Duplicate dataset ids found in different reflection tables. \n'
      'These will be treated as coming from separate datasets, and \n'
      'new dataset ids will be assigned for the whole dataset. \n')
      dataset_id_list = range(len(dataset_id_list))
  return single_reflection_tables, dataset_id_list

def _get_next_unique_id(unique_id, used_ids):
  """Test a list of used id strings to see if it contains str(unique_id),
  where unique_id is an integer. Returns the input unique id if it is not in
  the used_ids list, else it increments the unique_id by one until the value is
  not found in the list and then returns that."""
  if not str(unique_id) in used_ids:
    return unique_id
  else:
    unique_id += 1
    return _get_next_unique_id(unique_id, used_ids)

def assign_unique_identifiers(experiments, reflections):
  """Read in an experiment list and a list of reflection tables containing
  single datasets. The returned list of reflection tables will always have
  refl['id'] set to the position in the list.
  If the experiments have unique identifiers set, then this will be set for the
  reflection tables as well. If there are no unique identifiers, then this is
  set as the string of the position in the list e.g '0', '1', etc. If some
  experiments have identifiers, these will be maintined, and the other
  experiments will be given string ids of increasing integers, but skipping
  already existing values."""
  used_ids = []
  for exp, refl in zip(experiments, reflections):
    if exp.identifier != '':
      assert refl.are_experiment_identifiers_consistent()
      used_ids.append(exp.identifier)
  if len(set(used_ids)) == len(reflections):
    #all experiments have unique ids, so don't need to assign any.
    for i, (exp, refl) in enumerate(zip(experiments, reflections)):
      refl.experiment_identifiers()[i] = exp.identifier
      refl['id'] = flex.int(refl.size(), i) #make all unique
  elif used_ids: #some identifiers set
    unique_id = 0
    for i, (exp, refl) in enumerate(zip(experiments, reflections)):
      if exp.identifier != '':
        refl.experiment_identifiers()[i] = exp.identifier
      else:
        unique_id = _get_next_unique_id(unique_id, used_ids)
        strid = '%i' % unique_id
        exp.identifier = strid
        refl.experiment_identifiers()[i] = strid
        unique_id += 1
      refl['id'] = flex.int(refl.size(), i)
  else: #no identifiers set, so set all as str(int) of location in list.
    for i, (exp, refl) in enumerate(zip(experiments, reflections)):
      strid = '%i' % i
      exp.identifier = strid
      refl.experiment_identifiers()[i] = strid
      refl['id'] = flex.int(refl.size(), i)
  return experiments, reflections

def select_datasets_on_ids(experiments, reflections,
  exclude_datasets=None, use_datasets=None):
  """Select a subset of the dataset based on the use_datasets and
  exclude_datasets params options."""
  unique_identifiers = list(experiments.identifiers())
  if use_datasets or exclude_datasets:
    assert not (use_datasets and exclude_datasets), """
      The options use_datasets and exclude_datasets cannot be used in conjuction."""
    if exclude_datasets:
      assert all(i in unique_identifiers for i in exclude_datasets), """
      id not found in reflection tables"""
      reverse_ids = sorted(exclude_datasets,
        reverse=True)
      for id_ in reverse_ids:
        logger.info("Removing dataset %s.", id_)
        index = unique_identifiers.index(id_)
        del experiments[index]
        del reflections[index]
        #del dataset_ids[index]
      return experiments, reflections#, dataset_ids
    elif use_datasets:
      assert all(i in unique_identifiers for i in use_datasets), """
      id not found in reflection tables."""
      new_experiments = ExperimentList()
      new_reflections = []
      #new_dataset_ids = []
      for id_ in use_datasets:
        logger.info("Using dataset %s for scaling.", id_)
        index = unique_identifiers.index(id_)
        new_experiments.append(experiments[index])
        new_reflections.append(reflections[index])
        #new_dataset_ids.append(dataset_ids[index])
      return new_experiments, new_reflections#, new_dataset_ids
  else:
    return experiments, reflections#, dataset_ids

'''def calc_sigmasq(jacobian_transpose, var_cov):
  sigmasq = flex.float([])
  #note: must be a faster way to do this next bit? - in c++?
  for col in jacobian_transpose.cols(): #iterating over reflections
    a = flex.double(col.as_dense_vector())
    var = (a * var_cov) * a
    sigmasq.append(flex.sum(var))
  return sigmasq.as_double()'''

def calc_crystal_frame_vectors(reflection_table, experiments):
  """Calculate the diffraction vectors in the crystal frame."""
  reflection_table['s0'] = flex.vec3_double(
    [experiments.beam.get_s0()]*len(reflection_table))
  rot_axis = flex.vec3_double([experiments.goniometer.get_rotation_axis()])
  angles = reflection_table['phi'] * -1.0 * pi / 180 #want to do an inverse rot.
  reflection_table['s1c'] = rotate_vectors_about_axis(rot_axis,
    reflection_table['s1'], angles)
  reflection_table['s0c'] = rotate_vectors_about_axis(rot_axis,
    reflection_table['s0'], angles)
  reflection_table['s1c'] = align_rotation_axis_along_z(rot_axis,
    reflection_table['s1c'])
  reflection_table['s0c'] = align_rotation_axis_along_z(rot_axis,
    reflection_table['s0c'])
  return reflection_table

def align_rotation_axis_along_z(exp_rot_axis, vectors):
  """Rotate the coordinate system such that the exp_rot_axis is along z."""
  if list(exp_rot_axis) == [(0.0, 0.0, 1.0)]:
    return vectors
  (ux, uy, uz) = exp_rot_axis[0][0], exp_rot_axis[0][1], exp_rot_axis[0][2]
  cross_prod_uz = flex.vec3_double([(uy, -1.0*ux, 0.0)])
  angle_between_u_z = +1.0 * acos(uz/((ux**2 + uy**2 + uz**2)**0.5))
  phi = flex.double(vectors.size(), angle_between_u_z)
  new_vectors = rotate_vectors_about_axis(cross_prod_uz, vectors, phi)
  return flex.vec3_double(new_vectors)

def sph_harm_table(reflection_table, experiments, lmax):
  """Calculate the spherical harmonic table for a spherical
    harmonic absorption correction."""
  reflection_table = calc_crystal_frame_vectors(reflection_table, experiments)
  theta_phi = calc_theta_phi(reflection_table['s0c'])
  theta_phi_2 = calc_theta_phi(reflection_table['s1c'])
  sph_h_t = create_sph_harm_table(theta_phi, theta_phi_2, lmax)
  return sph_h_t


def calc_normE2(reflection_table, experiments):
  '''calculate normalised intensity values for centric and acentric reflections'''
  logger.info('Calculating normalised intensity values to select a reflection \n'
    'subset for scaling. \n')
  logger.debug('Negative intensities are set to zero for the purpose of \n'
    'calculating mean intensity values for resolution bins. This is to avoid \n'
    'spuriously high E^2 values due to a mean close to zero and should only \n'
    'affect the E^2 values of the highest resolution bins. \n')

  bad_refl_sel = reflection_table.get_flags(
    reflection_table.flags.bad_for_scaling, all=False)
  rt_subset = reflection_table.select(~bad_refl_sel)

  # Scaling subset is data that has not been flagged as bad or excluded
  crystal_symmetry = crystal.symmetry(
    unit_cell=experiments.crystal.get_unit_cell().parameters(),
    space_group=experiments.crystal.get_space_group())
  miller_set = miller.set(crystal_symmetry=crystal_symmetry,
    indices=rt_subset['miller_index'])
  rt_subset['resolution'] = 1.0/rt_subset['d']**2

  #handle negative reflections to minimise effect on mean I values.
  rt_subset['intensity_for_norm'] = copy.deepcopy(rt_subset['intensity'])
  rt_subset['intensity_for_norm'].set_selected(rt_subset['intensity'] < 0.0, 0.0)
  miller_array = miller.array(miller_set, data=rt_subset['intensity_for_norm'])

  #set up binning objects
  rt_subset['centric_flag'] = miller_array.centric_flags().data()
  n_centrics = rt_subset['centric_flag'].count(True)
  n_acentrics = rt_subset.size() - n_centrics

  if n_acentrics > 20000 or n_centrics > 20000:
    n_refl_shells = 20
  elif n_acentrics > 15000 or n_centrics > 15000:
    n_refl_shells = 15
  elif n_acentrics < 10000:
    reflection_table['Esq'] = flex.double(reflection_table.size(), 1.0)
    del reflection_table['intensity_for_norm']
    del reflection_table['centric_flag']
    del reflection_table['resolution']
    msg = ('No normalised intensity values were calculated, as an {sep}'
    'insufficient number of reflections were detected. {sep}'
    ).format(sep='\n')
    logger.info(msg)
    return reflection_table
  else:
    n_refl_shells = 10

  #calculate normalised intensities: first calculate bin averages
  step = ((max(rt_subset['resolution']) - min(rt_subset['resolution'])
           + 1e-8) / n_refl_shells)
  if n_centrics:
    centrics_array = miller_array.select_centric()
    centric_binner = centrics_array.setup_binner_d_star_sq_step(
      d_star_sq_step=step)
    mean_centric_values = centrics_array.mean(use_binning=centric_binner)
    mean_centric_values = mean_centric_values.data[1:-1]
    centric_bin_limits = centric_binner.limits()

  if n_acentrics:
    acentrics_array = miller_array.select_acentric()
    acentric_binner = acentrics_array.setup_binner_d_star_sq_step(
      d_star_sq_step=step)
    mean_acentric_values = acentrics_array.mean(use_binning=acentric_binner)
    mean_acentric_values = mean_acentric_values.data[1:-1]
    acentric_bin_limits = acentric_binner.limits()

  #now calculate normalised intensity values for full reflection table
  miller_set = miller.set(crystal_symmetry=crystal_symmetry,
    indices=reflection_table['miller_index'])
  reflection_table['Esq'] = flex.double(reflection_table.size(), 0.0)
  miller_array = miller.array(miller_set)
  reflection_table['centric_flag'] = miller_array.centric_flags().data()
  d0_sel = reflection_table['d'] == 0.0
  reflection_table['d'].set_selected(d0_sel, 1.0)  #set for now, then set back to zero later
  reflection_table['resolution'] = 1.0/reflection_table['d']**2

  if n_centrics:
    sel1 = reflection_table['centric_flag']
    for i in range(0, len(centric_bin_limits)-1):
      sel2 = reflection_table['resolution'] > centric_bin_limits[i]
      sel3 = reflection_table['resolution'] <= centric_bin_limits[i+1]
      sel = sel1 & sel2 & sel3
      intensities = reflection_table['intensity'].select(sel)
      reflection_table['Esq'].set_selected(sel, intensities/ mean_centric_values[i])
  if n_acentrics:
    sel1 = ~reflection_table['centric_flag']
    for i in range(0, len(acentric_bin_limits)-1):
      sel2 = reflection_table['resolution'] > acentric_bin_limits[i]
      sel3 = reflection_table['resolution'] <= acentric_bin_limits[i+1]
      sel = sel1 & sel2 & sel3
      intensities = reflection_table['intensity'].select(sel)
      reflection_table['Esq'].set_selected(sel, intensities/ mean_acentric_values[i])
  del reflection_table['intensity_for_norm']
  del reflection_table['centric_flag']
  del reflection_table['resolution']
  reflection_table['d'].set_selected(d0_sel, 0.0)
  reflection_table['Esq'].set_selected(d0_sel, 0.0)
  msg = ('The number of centric & acentric reflections is {0} & {1}, {sep}'
    '{2} resolution bins were used for the E^2 calculation. {sep}'
    ).format(n_centrics, n_acentrics, n_refl_shells, sep='\n')
  logger.debug(msg)
  return reflection_table

def set_wilson_outliers(reflection_table):
  """Function that takes in a reflection table with 'Esq' and 'centric_flag'
  values and sets an outlier flag depending on a cutoff for p < 1e-6."""

  centric_cutoff = 23.91
  sel1 = reflection_table['centric_flag']
  sel2 = reflection_table['Esq'] > centric_cutoff #probability <10^-6
  reflection_table.set_flags(sel1 & sel2,
    reflection_table.flags.outlier_in_scaling)

  acentric_cutoff = 13.82
  sel1 = ~reflection_table['centric_flag']
  sel2 = reflection_table['Esq'] > acentric_cutoff #probability <10^-6
  reflection_table.set_flags(sel1 & sel2,
    reflection_table.flags.outlier_in_scaling)
  msg = ('{0} reflections have been identified as outliers based on their normalised {sep}'
    'intensity values. These are reflections that have a probablity of {sep}'
    '< 10e-6 based on a Wilson distribution (E^2 > {1}, {2} for centric {sep}'
    'and acentric reflections respectively). {sep}'
    ).format(reflection_table.get_flags(
    reflection_table.flags.outlier_in_scaling).count(True),
    centric_cutoff, acentric_cutoff, sep='\n')
  logger.info(msg)
  return reflection_table

'''def R_pim_meas(scaler):
  #Calculate R_pim, R_meas from a scaler
  Ihl = scaler.Ih_table.intensities
  gvalues = scaler.Ih_table.inverse_scale_factors

  ones = flex.double([1.0] * len(Ihl))
  nh = ones * scaler.Ih_table.h_index_matrix

  I_average = (((Ihl/gvalues) * scaler.Ih_table.h_index_matrix)/nh)
  I_average_expanded = flex.double(np.repeat(I_average,
    scaler.Ih_table.h_index_counter_array))

  diff = abs((Ihl/gvalues) - I_average_expanded)
  reduced_diff = diff * scaler.Ih_table.h_index_matrix

  selection = (nh != 1.0)
  sel_reduced_diff = reduced_diff.select(selection)
  sel_nh = nh.select(selection)

  Rpim_upper = flex.sum(((1.0/(sel_nh - 1.0))**0.5) * sel_reduced_diff)
  Rmeas_upper = flex.sum(((sel_nh/(sel_nh - 1.0))**0.5) * sel_reduced_diff)
  sumIh = I_average_expanded * scaler.Ih_table.h_index_matrix
  sumIh = sumIh.select(selection)
  Rpim_lower = flex.sum(sumIh)
  Rpim = Rpim_upper/Rpim_lower
  Rmeas = Rmeas_upper/Rpim_lower
  return Rpim, Rmeas'''