import math
import string

import pytest

from skills.human_typer.distributions import (
    QWERTY_NEIGHBORS,
    log_normal,
    nearby_key,
    sample_iki,
    standard_normal,
    wpm_to_log_normal_params,
)


class TestStandardNormal:
    def test_produces_values_with_mean_near_zero(self):
        samples = [standard_normal() for _ in range(10_000)]
        mean = sum(samples) / len(samples)
        assert abs(mean) < 0.1

    def test_produces_values_with_std_dev_near_one(self):
        samples = [standard_normal() for _ in range(10_000)]
        mean = sum(samples) / len(samples)
        variance = sum((x - mean) ** 2 for x in samples) / len(samples)
        std_dev = math.sqrt(variance)
        assert abs(std_dev - 1) < 0.1


class TestLogNormal:
    def test_produces_only_positive_values(self):
        samples = [log_normal(5, 0.5) for _ in range(1_000)]
        for s in samples:
            assert s > 0

    def test_mean_approximates_expected(self):
        mu = 5
        sigma = 0.3
        expected_mean = math.exp(mu + (sigma * sigma) / 2)
        samples = [log_normal(mu, sigma) for _ in range(20_000)]
        mean = sum(samples) / len(samples)
        # Allow 5% tolerance
        assert abs(mean - expected_mean) / expected_mean < 0.05


class TestWpmToLogNormalParams:
    def test_60_wpm_yields_mean_iki_around_200ms(self):
        mu, sigma = wpm_to_log_normal_params(60, 15)
        expected_mean = math.exp(mu + (sigma * sigma) / 2)
        # 60 WPM = 300 chars/min = 200ms per char
        assert abs(expected_mean - 200) < 10

    def test_faster_wpm_yields_shorter_iki(self):
        slow_mu, slow_sigma = wpm_to_log_normal_params(40, 10)
        fast_mu, fast_sigma = wpm_to_log_normal_params(80, 10)
        slow_mean = math.exp(slow_mu + (slow_sigma * slow_sigma) / 2)
        fast_mean = math.exp(fast_mu + (fast_sigma * fast_sigma) / 2)
        assert fast_mean < slow_mean


class TestSampleIKI:
    def test_samples_are_positive(self):
        for _ in range(100):
            assert sample_iki(60, 15) > 0

    def test_mean_is_in_reasonable_range_for_60_wpm(self):
        samples = [sample_iki(60, 15) for _ in range(5_000)]
        mean = sum(samples) / len(samples)
        # 60 WPM -> ~200ms mean IKI, allow wide tolerance
        assert mean > 100
        assert mean < 400


class TestNearbyKey:
    def test_returns_a_neighbor_for_known_keys(self):
        neighbors = QWERTY_NEIGHBORS["t"]
        result = nearby_key("t")
        assert result in neighbors

    def test_preserves_uppercase(self):
        result = nearby_key("T")
        assert result == result.upper()

    def test_returns_a_letter_for_keys_without_neighbors(self):
        # Space has no neighbors, should get a random letter
        result = nearby_key(" ")
        assert len(result) == 1

    def test_never_returns_the_original_key(self):
        same_count = 0
        for _ in range(100):
            if nearby_key("f") == "f":
                same_count += 1
        # "f" is not in its own neighbor list, so this should always be 0
        assert same_count == 0
