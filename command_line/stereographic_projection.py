from __future__ import division

import math
from cctbx.array_family import flex
import iotbx.phil
from cctbx import crystal, miller
from scitbx import matrix

master_phil_scope = iotbx.phil.parse(
"""
hkl = None
  .type = ints(size=3)
  .multiple=True
hkl_limit = None
  .type = int(value_min=1)
expand_to_p1 = True
  .type = bool
  .help = "Expand the given miller indices to symmetry equivalent reflections"
eliminate_sys_absent = False
  .type = bool
  .help = "Eliminate systematically absent reflections"
plane_normal = None
  .type = ints(size=3)
save_coordinates = True
  .type = bool
plot = True
  .type = bool
label_indices = False
  .type = bool
frame = laboratory *crystal
  .type = choice
""")

def reference_poles_perpendicular_to_beam(beam, goniometer):
  # plane normal
  d0 = matrix.col(beam.get_s0()).normalize()
  d1 = d0.cross(matrix.col(goniometer.get_rotation_axis())).normalize()
  d2 = d1.cross(d0).normalize()
  return (d0, d1, d2)

def reference_poles_crystal(crystal_model, plane_normal=(0,0,1)):
  A = crystal_model.get_A()
  B = crystal_model.get_B()
  A_inv = A.inverse()
  G = A_inv * A_inv.transpose()
  G_star = A.transpose() * A
  h0 = (G * matrix.col(plane_normal)).normalize()
  h1 = matrix.col((1,0,0)).cross((G_star * h0).normalize())
  h2 = (G_star * h1).cross(G_star * h0).normalize()
  return tuple((B * h).normalize() for h in (h0, h1, h2))

def stereographic_projection(points, reference_poles):
  # http://dx.doi.org/10.1107/S0021889868005029
  # J. Appl. Cryst. (1968). 1, 68-70
  # The construction of stereographic projections by computer
  # G. K. Stokes, S. R. Keown and D. J. Dyson

  assert len(reference_poles) == 3
  r_0, r_1, r_2 = reference_poles

  projections = flex.vec2_double()

  for p in points:
    r_i = matrix.col(p)
    # theta is the angle between r_i and the plane normal, r_0
    cos_theta = r_i.cos_angle(r_0)
    if cos_theta < 0:
      r_i = -r_i
      cos_theta = r_i.cos_angle(r_0)

    # alpha is the angle between r_i and r_1
    cos_alpha = r_i.cos_angle(r_1)
    theta = math.acos(cos_theta)
    cos_phi = cos_alpha/math.sin(theta)
    if abs(cos_phi) > 1:
      cos_phi = math.copysign(1, cos_phi)
    phi = math.acos(cos_phi)

    N = r_i.dot(r_2)
    r = math.tan(theta/2)
    x = r * cos_phi
    y = r * math.sin(phi)
    y = math.copysign(y, N)

    projections.append((x,y))

  return projections


def gcd_list(l):
  # greatest common divisor for a list of numbers
  from scitbx.math import gcd_int_simple as gcd
  result = l[0]
  for i in range(1, len(l)):
    result = gcd(result, l[i])
  return result


def run(args):
  from dials.util.options import OptionParser
  from dials.util.options import flatten_experiments

  parser = OptionParser(
    phil=master_phil_scope,
    read_experiments=True,
    check_format=False)

  params, options = parser.parse_args(show_diff_phil=True)
  experiments = flatten_experiments(params.input.experiments)
  assert len(experiments) > 0

  if len(params.hkl) == 0 and params.hkl_limit is None:
    from libtbx.utils import Sorry
    raise Sorry("Please provide hkl or hkl_limit parameters.")

  if params.hkl is not None and len(params.hkl):
    miller_indices = flex.miller_index(params.hkl)
  elif params.hkl_limit is not None:
    limit = params.hkl_limit
    miller_indices = flex.miller_index()
    for h in range(-limit, limit+1):
      for k in range(-limit, limit+1):
        for l in range(-limit, limit+1):
          if (h,k,l) == (0,0,0): continue
          miller_indices.append((h,k,l))

  crystals = experiments.crystals()

  symmetry = crystal.symmetry(
    unit_cell=crystals[0].get_unit_cell(),
    space_group=crystals[0].get_space_group())
  miller_set = miller.set(symmetry, miller_indices)
  d_spacings = miller_set.d_spacings()
  if params.eliminate_sys_absent:
    d_spacings = d_spacings.eliminate_sys_absent()
  if params.expand_to_p1:
    d_spacings = d_spacings.as_non_anomalous_array().expand_to_p1()
    d_spacings = d_spacings.generate_bijvoet_mates()
  miller_indices = d_spacings.indices()

  # find the greatest common factor (divisor) between miller indices
  miller_indices_unique = flex.miller_index()
  for hkl in miller_indices:
    gcd = gcd_list(hkl)
    if gcd > 1:
      miller_indices_unique.append(tuple(int(h/gcd) for h in hkl))
    elif gcd < 1:
      pass
    else:
      miller_indices_unique.append(hkl)
  miller_indices = miller_indices_unique
  miller_indices = flex.miller_index(list(set(miller_indices)))

  ref_crystal = crystals[0]
  A = ref_crystal.get_A()
  U = ref_crystal.get_U()
  B = ref_crystal.get_B()

  if params.frame == 'laboratory':
    reference_poles = reference_poles_perpendicular_to_beam(
      experiments.beams()[0], experiments.goniometers()[0])
  else:
    if params.plane_normal is not None:
      plane_normal = params.plane_normal
    else:
      plane_normal = (0,0,1)
    reference_poles = reference_poles_crystal(
      ref_crystal, plane_normal=plane_normal)

  if params.frame == 'crystal':
    U = matrix.identity(3)

  reciprocal_space_points = list(U * B) * miller_indices.as_vec3_double()
  projections_ref = stereographic_projection(
    reciprocal_space_points, reference_poles)

  projections_all = [projections_ref]

  if len(crystals) > 0:
    from dials.algorithms.indexing.compare_orientation_matrices import \
        difference_rotation_matrix_and_euler_angles

    for cryst in crystals[1:]:
      if params.frame == 'crystal':
        R_ij, euler_angles, cb_op = difference_rotation_matrix_and_euler_angles(
          ref_crystal, cryst)
        U = R_ij
      else:
        U = cryst.get_U()
      reciprocal_space_points = list(U * cryst.get_B()) * miller_indices.as_vec3_double()
      projections = stereographic_projection(
        reciprocal_space_points, reference_poles)
      projections_all.append(projections)

  if params.save_coordinates:
    with open('projections.txt', 'wb') as f:
      print >> f, "crystal h k l x y"
      for i_cryst, projections in enumerate(projections_all):
        for hkl, proj in zip(miller_indices, projections):
          print >> f, "%i" %(i_cryst+1),
          print >> f, "%i %i %i" %hkl,
          print >> f, "%f %f" %proj

  if params.plot:
    from matplotlib import pyplot
    from matplotlib import pylab

    cir = pylab.Circle((0,0), radius=1.0, fill=False, color='0.75')
    pylab.gca().add_patch(cir)

    for projections in projections_all:
      x, y = projections.parts()
      pyplot.scatter(x.as_numpy_array(), y.as_numpy_array(), c='b', s=2)
      if params.label_indices:
        for hkl, proj in zip(miller_indices, projections):
          pyplot.text(proj[0], proj[1], str(hkl), fontsize=10)
    pyplot.axes().set_aspect('equal')
    pyplot.xlim(-1.1,1.1)
    pyplot.ylim(-1.1,1.1)
    pyplot.show()


if __name__ == '__main__':
  import sys
  run(sys.argv[1:])
