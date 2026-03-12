"""
Histogram classifier for detecting non-normal distributions.

This module contains functions to classify histograms based on multiple criteria:
- Multiple peaks (bimodal/multimodal)
- High skewness (asymmetry)
- Abnormal kurtosis (tail heaviness)
- Secondary humps/shoulders
"""

import numpy as np

# Thresholds for classification
kurtosis_threshold = 1
skewness_threshold = 0.2
LOW_LIGHT_MEAN_THRESHOLD = (
    75  # Light histogram with mean below this is "Low Light" (do not save)
)


def find_peaks_simple(signal, height=None, distance=None, prominence=None):
    """
    Simple peak detection algorithm using numpy only.
    Finds local maxima that meet height, distance, and prominence criteria.

    Args:
        signal (array): Input signal
        height (float): Minimum height of peaks
        distance (int): Minimum distance between peaks
        prominence (float): Minimum prominence of peaks

    Returns:
        tuple: (peaks: array, properties: dict)
    """
    signal = np.array(signal)

    # Find local maxima (peaks)
    # A point is a peak if it's greater than its neighbors
    peaks = []
    for i in range(1, len(signal) - 1):
        if signal[i] > signal[i - 1] and signal[i] > signal[i + 1]:
            peaks.append(i)

    if len(peaks) == 0:
        return np.array([]), {}

    peaks = np.array(peaks)

    # Filter by height
    if height is not None:
        peak_heights = signal[peaks]
        peaks = peaks[peak_heights >= height]

    if len(peaks) == 0:
        return np.array([]), {}

    # Filter by distance (keep only peaks that are far enough apart)
    if distance is not None and len(peaks) > 1:
        filtered_peaks = [peaks[0]]
        for peak in peaks[1:]:
            if peak - filtered_peaks[-1] >= distance:
                filtered_peaks.append(peak)
        peaks = np.array(filtered_peaks)

    # Filter by prominence
    # Prominence is the height of the peak above the higher of the two surrounding minima
    if prominence is not None and len(peaks) > 0:
        filtered_peaks = []
        for peak in peaks:
            peak_val = signal[peak]

            # Find minimum on left side (up to previous peak or start)
            left_min = np.min(signal[: peak + 1]) if peak > 0 else peak_val

            # Find minimum on right side (up to next peak or end)
            right_min = np.min(signal[peak:]) if peak < len(signal) - 1 else peak_val

            # Prominence is peak height minus the higher of the two surrounding minima
            surrounding_min = max(left_min, right_min)
            peak_prominence = peak_val - surrounding_min

            if peak_prominence >= prominence:
                filtered_peaks.append(peak)

        peaks = np.array(filtered_peaks)

    properties = {"peak_heights": signal[peaks] if len(peaks) > 0 else np.array([])}
    return peaks, properties


def histogram_weighted_mean(histogram_values):
    """
    Compute the weighted mean (first moment) of the histogram.
    Uses bin index as value and histogram count as weight.

    Args:
        histogram_values (array): Histogram bin counts (length 1024)

    Returns:
        float: Weighted mean bin index, or 0.0 if empty.
    """
    histogram_values = np.asarray(histogram_values)
    total = np.sum(histogram_values)
    if total == 0:
        return 0.0
    bins = np.arange(len(histogram_values), dtype=float)
    return float(np.sum(bins * histogram_values) / total)


def calculate_skewness(histogram_values):
    """
    Calculate skewness of a distribution.
    Normal distributions have skewness ≈ 0.

    Args:
        histogram_values (array): Histogram bin values

    Returns:
        float: Skewness value
    """
    # Create weighted distribution
    bins = np.arange(len(histogram_values))
    total = np.sum(histogram_values)
    if total == 0:
        return 0.0

    # Calculate weighted mean
    mean = np.sum(bins * histogram_values) / total

    # Calculate weighted variance
    variance = np.sum(histogram_values * (bins - mean) ** 2) / total

    if variance == 0:
        return 0.0

    std = np.sqrt(variance)

    # Calculate skewness (third moment)
    skewness = np.sum(histogram_values * ((bins - mean) / std) ** 3) / total

    return skewness


def calculate_kurtosis(histogram_values):
    """
    Calculate kurtosis of a distribution.
    Normal distributions have kurtosis ≈ 3 (excess kurtosis ≈ 0).

    Args:
        histogram_values (array): Histogram bin values

    Returns:
        float: Kurtosis value
    """
    # Create weighted distribution
    bins = np.arange(len(histogram_values))
    total = np.sum(histogram_values)
    if total == 0:
        return 3.0  # Normal distribution kurtosis

    # Calculate weighted mean and std
    mean = np.sum(bins * histogram_values) / total
    variance = np.sum(histogram_values * (bins - mean) ** 2) / total

    if variance == 0:
        return 3.0

    std = np.sqrt(variance)

    # Calculate kurtosis (fourth moment)
    kurtosis = np.sum(histogram_values * ((bins - mean) / std) ** 4) / total

    return kurtosis


def detect_secondary_hump(histogram_values, max_position):
    """
    Detect if there's a secondary hump/shoulder in the distribution,
    even without a clear peak. This catches cases where there's a
    second hump that doesn't form a distinct peak.

    Args:
        histogram_values (array): Histogram bin values
        max_position (int): Position of the main peak

    Returns:
        tuple: (has_secondary_hump: bool, hump_position: int or None)
    """
    # Smooth the histogram
    window_size = 5
    if len(histogram_values) > window_size:
        smoothed = np.convolve(
            histogram_values, np.ones(window_size) / window_size, mode="same"
        )
    else:
        smoothed = histogram_values

    max_value = np.max(smoothed)
    if max_value == 0:
        return False, None

    # Find the main peak region (around max_position)
    # Look for a secondary hump on the other side of the distribution
    # A hump is a region where values are significantly elevated

    # Split the distribution into left and right halves relative to the main peak
    left_side = smoothed[:max_position]
    right_side = smoothed[max_position + 1 :]

    # Threshold for significant elevation: at least 15% of max value
    threshold = max_value * 0.15

    # Check left side for secondary hump
    if len(left_side) > 50:
        # Find the maximum in the left half (excluding the edge near main peak)
        left_half = left_side[: len(left_side) // 2]
        if len(left_half) > 0:
            left_max_idx_in_half = np.argmax(left_half)
            left_max_val = left_half[left_max_idx_in_half]
            if left_max_val >= threshold:
                # Check if this is a distinct hump (not just noise)
                # Look for a region around this max that's elevated
                region_start = max(0, left_max_idx_in_half - 20)
                region_end = min(len(left_half), left_max_idx_in_half + 20)
                region_avg = np.mean(left_half[region_start:region_end])
                if region_avg >= threshold * 0.7:
                    # Return absolute position in the full array
                    return True, left_max_idx_in_half

    # Check right side for secondary hump
    if len(right_side) > 50:
        # Find the maximum in the right half (excluding the edge near main peak)
        right_half = right_side[len(right_side) // 2 :]
        if len(right_half) > 0:
            right_max_idx_in_half = np.argmax(right_half)
            right_max_idx = len(right_side) // 2 + right_max_idx_in_half
            right_max_val = right_half[right_max_idx_in_half]
            if right_max_val >= threshold:
                # Check if this is a distinct hump
                region_start = max(0, right_max_idx_in_half - 20)
                region_end = min(len(right_half), right_max_idx_in_half + 20)
                region_avg = np.mean(right_half[region_start:region_end])
                if region_avg >= threshold * 0.7:
                    return True, max_position + 1 + right_max_idx

    return False, None


def check_non_normal(histogram_values):
    """
    Check if a histogram deviates from a normal distribution.
    Uses multiple criteria:
    1. Multiple peaks (bimodal/multimodal)
    2. High skewness (asymmetry)
    3. Abnormal kurtosis (tail heaviness)
    4. Secondary humps/shoulders

    Args:
        histogram_values (array): Histogram bin values

    Returns:
        tuple: (is_non_normal: bool, num_peaks: int, peak_positions: list,
                reasons: list, skewness: float, kurtosis: float)
    """
    reasons = []

    # Smooth the histogram slightly to reduce noise
    window_size = 5
    if len(histogram_values) > window_size:
        smoothed = np.convolve(
            histogram_values, np.ones(window_size) / window_size, mode="same"
        )
    else:
        smoothed = histogram_values

    max_value = np.max(smoothed)
    if max_value == 0:
        return False, 0, [], [], 0.0, 3.0

    max_position = np.argmax(smoothed)

    # 1. Check for multiple peaks (bimodal/multimodal)
    peaks, properties = find_peaks_simple(
        smoothed,
        height=max_value * 0.05,  # At least 5% of max
        distance=50,  # At least 50 bins apart
        prominence=max_value * 0.08,
    )  # Lower prominence to catch more cases

    num_peaks = len(peaks)
    peak_positions = peaks.tolist()

    if num_peaks >= 2:
        reasons.append(f"Multiple peaks ({num_peaks})")

    # 2. Check for skewness (normal distribution should have skewness ≈ 0)
    skewness = calculate_skewness(histogram_values)
    if abs(skewness) > skewness_threshold:  # Significant skewness
        reasons.append(f"High skewness ({skewness:.2f})")

    # 3. Check for kurtosis (normal distribution should have kurtosis ≈ 3)
    kurtosis = calculate_kurtosis(histogram_values)
    excess_kurtosis = kurtosis - 3.0
    if abs(excess_kurtosis) > kurtosis_threshold:  # Significant deviation from normal
        reasons.append(f"Abnormal kurtosis ({kurtosis:.2f})")

    # 4. Check for secondary humps/shoulders
    has_secondary_hump, hump_position = detect_secondary_hump(
        histogram_values, max_position
    )
    if has_secondary_hump:
        reasons.append("Secondary hump detected")
        if hump_position is not None and hump_position not in peak_positions:
            peak_positions.append(hump_position)
            peak_positions.sort()
            num_peaks = len(peak_positions)

    # Consider it non-normal if it fails any criterion
    is_non_normal = len(reasons) > 0
    print(
        f"is_non_normal: {is_non_normal}, num_peaks: {num_peaks}, peak_positions: {peak_positions}, reasons: {reasons}, skewness: {skewness}, kurtosis: {kurtosis}"
    )
    return is_non_normal, num_peaks, peak_positions, reasons, skewness, kurtosis


def classify_histogram(histogram_values, is_light_histogram: bool):
    """
    Classify a histogram for pass/fail or low-light.

    For light histograms (illuminated case): if weighted mean < LOW_LIGHT_MEAN_THRESHOLD,
    returns "LOW_LIGHT" — caller should show "Low Light" in grey and not save.

    Otherwise runs check_non_normal(); returns "PASS" if normal, "FAIL" if non-normal.

    Args:
        histogram_values (array): Histogram bin counts (e.g. length 1024)
        is_light_histogram (bool): True if this is an illuminated (light) capture

    Returns:
        str: "PASS", "FAIL", or "LOW_LIGHT"
    """
    if not is_light_histogram:
        is_non_normal, _, _, _, _, _ = check_non_normal(histogram_values)
        return "FAIL" if is_non_normal else "PASS"

    mean = histogram_weighted_mean(histogram_values)
    if mean < LOW_LIGHT_MEAN_THRESHOLD:
        return "LOW_LIGHT"

    is_non_normal, _, _, _, _, _ = check_non_normal(histogram_values)
    return "FAIL" if is_non_normal else "PASS"
