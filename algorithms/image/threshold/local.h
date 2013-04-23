/*
 * local.h
 *
 *  Copyright (C) 2013 Diamond Light Source
 *
 *  Author: James Parkhurst
 *
 *  This code is distributed under the BSD license, a copy of which is
 *  included in the root directory of this package.
 */
#ifndef DIALS_ALGORITHMS_IMAGE_THRESHOLD_UNIMODAL_H
#define DIALS_ALGORITHMS_IMAGE_THRESHOLD_UNIMODAL_H

#include <omp.h>

#include <cmath>
#include <iostream>
#include <scitbx/array_family/tiny_types.h>
#include <scitbx/array_family/flex_types.h>
#include <scitbx/array_family/ref_reductions.h>
#include <dials/error.h>
#include <dials/algorithms/image/filter/mean_and_variance.h>
#include <dials/algorithms/image/filter/fano_filter.h>

namespace dials { namespace algorithms {

  /**
   * Threshold the image using the niblack method.
   *
   * pixel > mean + n_sigma * sdev ? object : background
   *
   * @param image The image to threshold
   * @param size The size of the local area
   * @param n_sigma The number of standard deviations
   * @returns The thresholded image
   */
  inline
  flex_bool niblack(const flex_double &image, int2 size, double n_sigma) {

    // Check the input
    DIALS_ASSERT(n_sigma >= 0);

    // Calculate the mean and variance filtered images
    MeanAndVarianceFilter filter(image, size);
    flex_double mean = filter.mean();
    flex_double var = filter.sample_variance();

    // Assign the pixels to object and background
    flex_bool result(image.accessor());
    #pragma omp parallel for      
    for (std::size_t i = 0; i < var.size(); ++i) {
      result[i] = image[i] > mean[i] + n_sigma * sqrt(var[i]) ? 1 : 0;
    }

    // Return the thresholded image
    return result;
  }

  /**
   * Threshold the image using the sauvola method.
   *
   * pixel > mean * (1 + k * (sdev / (r - 1)))
   *
   * @param image The image to threshold
   * @param size The size of the local area
   * @param k A parameter
   * @param r A parameter
   * @returns The thresholded image
   */
  inline
  flex_bool sauvola(const flex_double &image, int2 size, double k, double r) {
    // Check the input
    DIALS_ASSERT(k >= 0 && r >= 0);

    // Calculate the mean and variance filtered images
    MeanAndVarianceFilter filter(image, size);
    flex_double mean = filter.mean();
    flex_double var = filter.sample_variance();

    // Assign the pixels to object and background
    flex_bool result(image.accessor());
    #pragma omp parallel for      
    for (std::size_t i = 0; i < var.size(); ++i) {
      result[i] = image[i] > mean[i] * (
        1.0 + k * (sqrt(var[i]) / r - 1)) ? 1 : 0;
    }

    // Return the thresholded image
    return result;
  }


  /**
   * Threshold the image using a fano filter. Essentially a test for objects
   * within a poisson distribution.
   *
   * var/mean > 1.0 + n_sigma * sqrt(2 / (n - 1)) ? object : background
   *
   * @param image The image to threshold
   * @param size The size of the local window
   * @param n_sigma The number of standard deviations.
   */
  inline
  flex_bool fano(const flex_double &image, int2 size, double n_sigma) {

    // Check the input
    DIALS_ASSERT(n_sigma >= 0);

    // Calculate the fano filtered image
    FanoFilter filter(image, size);
    flex_double fano_image = filter.fano();
    flex_int    fano_mask  = filter.mask();

    // Calculate the bound
    std::size_t n = (2 * size[0] + 1) * (2 * size[1] + 1);
    double bound = 1.0 + n_sigma * sqrt(2.0 / (n - 1));

    // Assign pixels to object or background
    flex_bool result(image.accessor());
    #pragma omp parallel for      
    for (std::size_t i = 0; i < image.size(); ++i) {
      result[i] = (fano_mask[i] && fano_image[i]) > bound ? 1 : 0;
    }

    // Return thresholded image
    return result;
  }

  /**
   * Threshold the image using a fano filter. Essentially a test for objects
   * within a poisson distribution.
   *
   * var/mean > 1.0 + n_sigma * sqrt(2 / (n - 1)) ? object : background
   *
   * @param image The image to threshold
   * @param mask The mask to use
   * @param size The size of the local window
   * @param min_count The minimum counts for a point to be valid
   * @param n_sigma The number of standard deviations.
   */
  inline
  flex_bool fano_masked(const flex_double &image, const flex_bool &mask,
      int2 size, int min_count, double n_sigma) {

    // Check the input
    DIALS_ASSERT(n_sigma >= 0);

    // Copy the mask into a temp variable
    flex_int temp(mask.accessor());
    #pragma omp parallel for
    for (std::size_t i = 0; i < temp.size(); ++i) {
      temp[i] = mask[i] ? 1 : 0;
    }

    // Calculate the masked fano filtered image
    FanoFilterMasked filter(image, temp, size, min_count);
    flex_double fano_image = filter.fano();
    flex_int    count      = filter.count();
    temp                   = filter.mask();

    // Assign pixels to object or background
    flex_bool result(image.accessor(), 0);
    #pragma omp parallel for      
    for (std::size_t i = 0; i < image.size(); ++i) {
      if (temp[i]) {
        double bound = 1.0 + n_sigma * sqrt(2.0 / (count[i] - 1));
        result[i] = (fano_image[i] > bound) ? 1 : 0;
      }
    }

    // Return thresholded image
    return result;
  }

  /**
   * Threshold the image using a gain filter. Same as the fano filter but
   * using a gain map for the calculation
   *
   * var/mean > g + n_sigma * 2 * g / sqrt(n - 1) ? object : background
   *
   * @param image The image to threshold
   * @param mask The mask to use
   * @param gain The gain map
   * @param size The size of the local window
   * @param min_count The minimum counts for a point to be valid
   * @param n_sigma The number of standard deviations.
   */
  inline
  flex_bool gain(const flex_double &image, const flex_bool &mask,
      flex_double gain, int2 size, int min_count, double n_sigma) {

    // Check the input
    DIALS_ASSERT(n_sigma >= 0);

    // Copy the mask into a temp variable
    flex_int temp(mask.accessor());
    #pragma omp parallel for
    for (std::size_t i = 0; i < temp.size(); ++i) {
      temp[i] = mask[i] ? 1 : 0;
    }

    // Calculate the masked fano filtered image
    FanoFilterMasked filter(image, temp, size, min_count);
    flex_double fano_image = filter.fano();
    flex_int    count      = filter.count();
    temp                   = filter.mask();

    // Assign pixels to object or background
    flex_bool result(image.accessor(), 0);
    #pragma omp parallel for      
    for (std::size_t i = 0; i < image.size(); ++i) {
      if (temp[i]) {
        double bound = gain[i] + n_sigma * gain[i] * sqrt(2.0 / (count[i] - 1));
        result[i] = (fano_image[i] > bound) ? 1 : 0;
      }
    }

    // Return thresholded image
    return result;
  }

  /**
   * Threshold the image as in xds. Same as the fano filter but
   * using a gain map for the calculation
   *
   * var/mean > g + n_sigma * 2 * g / sqrt(n - 1) && 
   * pixel > mean + sqrt(mean) ? object : background
   *
   * @param image The image to threshold
   * @param mask The mask to use
   * @param size The size of the local window
   * @param nsig_b The background threshold.
   * @param nsig_s The strong pixel threshold
   */
  inline
  flex_bool kabsch(const flex_double &image, const flex_bool &mask,
      int2 size, double nsig_b, double nsig_s) {

    // Check the input
    DIALS_ASSERT(nsig_b >= 0 && nsig_s >= 0);

    // Copy the mask into a temp variable
    flex_int temp(mask.accessor());
    #pragma omp parallel for
    for (std::size_t i = 0; i < temp.size(); ++i) {
      temp[i] = mask[i] ? 1 : 0;
    }

    // Calculate the masked fano filtered image
    FanoFilterMasked filter(image, temp, size, 0);
    flex_double fano_image = filter.fano();
    flex_double mean       = filter.mean();
    flex_int count         = filter.count();
    temp                   = filter.mask();

    // Assign pixels to object or background
    flex_bool result(image.accessor(), 0);
    #pragma omp parallel for      
    for (std::size_t i = 0; i < image.size(); ++i) {
      if (temp[i]) {
        double bnd_b = 1.0 + nsig_b * sqrt(2.0 / (count[i] - 1));
        double bnd_s = mean[i] + nsig_s * sqrt(mean[i]);
        result[i]  = (fano_image[i] > bnd_b && image[i] > bnd_s) ? 1 : 0;
      }
    }

    // Return thresholded image
    return result;
  }  
     
  /**
   * Threshold the image as in xds. Same as the fano filter but
   * using a gain map for the calculation
   *
   * var/mean > g + n_sigma * 2 * g / sqrt(n - 1) && 
   * pixel > mean + sqrt(gain * mean) ? object : background
   *
   * @param image The image to threshold
   * @param mask The mask to use
   * @param gain The gain map
   * @param size The size of the local window
   * @param nsig_b The background threshold.
   * @param nsig_s The strong pixel threshold
   */
  inline
  flex_bool kabsch_w_gain(const flex_double &image, const flex_bool &mask,
      flex_double gain, int2 size, double nsig_b, double nsig_s) {

    // Check the input
    DIALS_ASSERT(nsig_b >= 0 && nsig_s >= 0);

    // Copy the mask into a temp variable
    flex_int temp(mask.accessor());
    #pragma omp parallel for
    for (std::size_t i = 0; i < temp.size(); ++i) {
      temp[i] = mask[i] ? 1 : 0;
    }

    // Calculate the masked fano filtered image
    FanoFilterMasked filter(image, temp, size, 0);
    flex_double fano_image = filter.fano();
    flex_double mean       = filter.mean();
    flex_int count         = filter.count();
    temp                   = filter.mask();

    // Assign pixels to object or background
    flex_bool result(image.accessor(), 0);
    #pragma omp parallel for      
    for (std::size_t i = 0; i < image.size(); ++i) {
      if (temp[i]) {
        double bnd_b = gain[i] + nsig_b * gain[i] * sqrt(2.0 / (count[i] - 1));
        double bnd_s = mean[i] + nsig_s * sqrt(gain[i] * mean[i]);
        result[i]  = (fano_image[i] > bnd_b && image[i] > bnd_s) ? 1 : 0;
      }
    }

    // Return thresholded image
    return result;
  }  

}} // namespace dials::algorithms

#endif /* DIALS_ALGORITHMS_IMAGE_THRESHOLD_UNIMODAL_H */
