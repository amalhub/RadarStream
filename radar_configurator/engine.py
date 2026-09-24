"""Pure-Python IWR6843 configuration parsing, calculation and generation.

The formulas and cross-field checks are adapted from the Python version of
the TI mmWave Sensing Estimator supplied with this project.  This focused
engine deliberately emits only commands present in RadarStream's configured
template, so application-specific raw ADC/LVDS settings are preserved.
"""

from dataclasses import dataclass, replace
import math
from pathlib import Path


LIGHT_SPEED = 299_792_458.0


class ConfigValidationError(ValueError):
    """Raised when a generated radar configuration violates a constraint."""

    def __init__(self, issues):
        self.issues = tuple(issues)
        super().__init__("; ".join(self.issues))


@dataclass(frozen=True)
class Iwr6843ConfigValues:
    """Editable values represented by the compact RadarStream component."""

    start_frequency_ghz: float = 60.0
    idle_time_us: float = 270.0
    adc_start_time_us: float = 6.0
    ramp_end_time_us: float = 40.0
    frequency_slope_mhz_us: float = 31.25
    adc_samples: int = 128
    sample_rate_ksps: int = 4000
    chirp_loops: int = 128
    frame_period_ms: float = 40.0
    rx_channel_mask: int = 1
    tx_channel_mask: int = 1


@dataclass(frozen=True)
class ConfigMetrics:
    """Calculated physical and capture properties for one configuration."""

    range_resolution_m: float
    max_range_m: float
    velocity_resolution_mps: float
    max_velocity_mps: float
    bandwidth_mhz: float
    chirp_rate_hz: float
    active_frame_time_ms: float
    frame_rate_hz: float
    rx_antennas: int
    tx_antennas: int


def _format_number(value):
    value = float(value)
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return "{:.10f}".format(value).rstrip("0").rstrip(".")


def _command_tokens(lines, command):
    return [
        line.split()
        for line in lines
        if line.strip() and line.split()[0] == command
    ]


def _bit_count(mask):
    return bin(int(mask)).count("1")


def _just_below(value):
    """Return a portable approximation of the next float below *value*."""
    return value - max(abs(value) * 1e-12, 1e-9)


def _just_above(value):
    """Return a portable approximation of the next float above *value*."""
    return value + max(abs(value) * 1e-12, 1e-9)


class Iwr6843ConfigEngine:
    """Load a CLI file, calculate its metrics and render a validated variant."""

    REQUIRED_COMMANDS = (
        "channelCfg",
        "adcCfg",
        "adcbufCfg",
        "profileCfg",
        "chirpCfg",
        "frameCfg",
    )

    def __init__(self, template_path):
        self.template_path = Path(template_path)

    def template_text(self):
        return self.template_path.read_text(encoding="utf-8")

    def default_values(self):
        return self.parse_text(self.template_text())

    def parse_file(self, config_path):
        return self.parse_text(Path(config_path).read_text(encoding="utf-8"))

    def parse_text(self, config_text):
        lines = config_text.splitlines()
        missing = [
            command
            for command in self.REQUIRED_COMMANDS
            if not _command_tokens(lines, command)
        ]
        if missing:
            raise ValueError(
                "Configuration is missing required commands: {}".format(", ".join(missing))
            )

        channel = _command_tokens(lines, "channelCfg")[0]
        profile = _command_tokens(lines, "profileCfg")[0]
        frame = _command_tokens(lines, "frameCfg")[0]
        if len(channel) < 4 or len(profile) < 15 or len(frame) < 8:
            raise ValueError("channelCfg, profileCfg, or frameCfg has incomplete arguments")

        return Iwr6843ConfigValues(
            start_frequency_ghz=float(profile[2]),
            idle_time_us=float(profile[3]),
            adc_start_time_us=float(profile[4]),
            ramp_end_time_us=float(profile[5]),
            frequency_slope_mhz_us=float(profile[8]),
            adc_samples=int(float(profile[10])),
            sample_rate_ksps=int(float(profile[11])),
            chirp_loops=int(float(frame[3])),
            frame_period_ms=float(frame[5]),
            rx_channel_mask=int(channel[1], 0),
            tx_channel_mask=int(channel[2], 0),
        )

    def metrics(self, values):
        sample_rate_msps = values.sample_rate_ksps / 1000.0
        rx_antennas = _bit_count(values.rx_channel_mask)
        tx_antennas = _bit_count(values.tx_channel_mask)
        chirp_time_us = (
            values.adc_samples / sample_rate_msps
            if sample_rate_msps > 0
            else math.inf
        )
        bandwidth_mhz = values.frequency_slope_mhz_us * chirp_time_us
        visible_if_mhz = sample_rate_msps * 0.8
        max_beat_frequency_mhz = min(visible_if_mhz, 10.0)
        carrier_frequency_ghz = (
            values.start_frequency_ghz
            + values.frequency_slope_mhz_us
            * values.adc_start_time_us
            / 1000.0
            + bandwidth_mhz / 2000.0
        )
        repetition_us = tx_antennas * (
            values.idle_time_us + values.ramp_end_time_us
        )
        range_resolution_m = (
            LIGHT_SPEED / (2.0 * bandwidth_mhz * 1e6)
            if bandwidth_mhz > 0
            else math.inf
        )
        max_range_m = (
            max_beat_frequency_mhz
            * LIGHT_SPEED
            / (2.0 * values.frequency_slope_mhz_us * 1e6)
            if values.frequency_slope_mhz_us > 0
            else math.inf
        )
        max_velocity_mps = (
            LIGHT_SPEED
            / (4.0 * carrier_frequency_ghz * 1e9 * repetition_us * 1e-6)
            if carrier_frequency_ghz > 0 and repetition_us > 0
            else math.inf
        )
        velocity_resolution_mps = (
            2.0 * max_velocity_mps / values.chirp_loops
            if values.chirp_loops > 0
            else math.inf
        )
        active_frame_time_ms = (
            tx_antennas
            * values.chirp_loops
            * (values.idle_time_us + values.ramp_end_time_us)
            / 1000.0
        )
        return ConfigMetrics(
            range_resolution_m=range_resolution_m,
            max_range_m=max_range_m,
            velocity_resolution_mps=velocity_resolution_mps,
            max_velocity_mps=max_velocity_mps,
            bandwidth_mhz=bandwidth_mhz,
            chirp_rate_hz=(
                1e6 / (values.idle_time_us + values.ramp_end_time_us)
                if values.idle_time_us + values.ramp_end_time_us > 0
                else math.inf
            ),
            active_frame_time_ms=active_frame_time_ms,
            frame_rate_hz=(
                1000.0 / values.frame_period_ms
                if values.frame_period_ms > 0
                else math.inf
            ),
            rx_antennas=rx_antennas,
            tx_antennas=tx_antennas,
        )

    def parameter_constraints(self, values):
        """Return the current valid interval for every editable parameter.

        Several IWR6843 limits depend on other fields.  Keeping those formulas
        beside :meth:`validate` lets the UI draw truthful limit markers instead
        of maintaining a second, eventually divergent set of rules.
        """
        sample_rate_msps = values.sample_rate_ksps / 1000.0
        sample_time_us = (
            values.adc_samples / sample_rate_msps
            if sample_rate_msps > 0
            else math.inf
        )
        available_sample_time_us = (
            values.ramp_end_time_us - values.adc_start_time_us
        )
        tx_antennas = max(_bit_count(values.tx_channel_mask), 1)
        chirp_time_us = values.idle_time_us + values.ramp_end_time_us

        start_frequency_max = (
            64.0
            - values.frequency_slope_mhz_us
            * values.ramp_end_time_us
            / 1000.0
        )

        slope_maxima = [266.0]
        if values.ramp_end_time_us > 0:
            slope_maxima.append(
                (64.0 - values.start_frequency_ghz)
                * 1000.0
                / values.ramp_end_time_us
            )
        if sample_time_us > 0 and math.isfinite(sample_time_us):
            slope_maxima.append(_just_below(4000.0 / sample_time_us))

        ramp_maxima = [1000.0]
        if values.frequency_slope_mhz_us > 0:
            ramp_maxima.append(
                (64.0 - values.start_frequency_ghz)
                * 1000.0
                / values.frequency_slope_mhz_us
            )
        if values.chirp_loops > 0:
            ramp_maxima.append(
                values.frame_period_ms
                * 1000.0
                / (tx_antennas * values.chirp_loops)
                - values.idle_time_us
            )

        adc_sample_maxima = [2048.0]
        if sample_rate_msps > 0:
            adc_sample_maxima.append(
                sample_rate_msps * available_sample_time_us
            )
        if values.frequency_slope_mhz_us > 0:
            adc_sample_maxima.append(
                _just_below(
                    4.0
                    * values.sample_rate_ksps
                    / values.frequency_slope_mhz_us
                )
            )

        sample_rate_minima = [100.0]
        if available_sample_time_us > 0:
            sample_rate_minima.append(
                values.adc_samples * 1000.0 / available_sample_time_us
            )
        if values.frequency_slope_mhz_us > 0:
            sample_rate_minima.append(
                _just_above(
                    values.frequency_slope_mhz_us * values.adc_samples / 4.0
                )
            )

        idle_time_max = math.inf
        if values.chirp_loops > 0:
            idle_time_max = (
                values.frame_period_ms
                * 1000.0
                / (tx_antennas * values.chirp_loops)
                - values.ramp_end_time_us
            )

        chirp_loops_max = math.inf
        if chirp_time_us > 0:
            chirp_loops_max = (
                values.frame_period_ms
                * 1000.0
                / (tx_antennas * chirp_time_us)
            )

        active_frame_time_ms = (
            tx_antennas
            * values.chirp_loops
            * chirp_time_us
            / 1000.0
        )

        return {
            "start_frequency_ghz": (60.0, min(64.0, start_frequency_max)),
            "idle_time_us": (0.1, min(1000.0, idle_time_max)),
            "adc_start_time_us": (
                0.0,
                min(200.0, values.ramp_end_time_us - sample_time_us),
            ),
            "ramp_end_time_us": (
                values.adc_start_time_us + sample_time_us,
                min(ramp_maxima),
            ),
            "frequency_slope_mhz_us": (0.1, min(slope_maxima)),
            "adc_samples": (2.0, min(adc_sample_maxima)),
            "sample_rate_ksps": (max(sample_rate_minima), 12500.0),
            "chirp_loops": (1.0, min(4096.0, chirp_loops_max)),
            "frame_period_ms": (
                max(0.1, active_frame_time_ms),
                10000.0,
            ),
        }

    def validate(self, values):
        metrics = self.metrics(values)
        issues = []
        numeric_values = tuple(values.__dict__.values())
        if not all(math.isfinite(float(value)) for value in numeric_values):
            return ("Non-finite numeric values are present in parameters",)
        if not 1 <= values.rx_channel_mask <= 15:
            issues.append("RX channel mask must be between 1 and 15")
        if not 1 <= values.tx_channel_mask <= 7:
            issues.append("TX channel mask must be between 1 and 7")
        if not 60 <= values.start_frequency_ghz <= 64:
            issues.append("IWR6843 start frequency must be between 60 and 64 GHz")
        end_frequency = (
            values.start_frequency_ghz
            + values.frequency_slope_mhz_us * values.ramp_end_time_us / 1000.0
        )
        if end_frequency > 64:
            issues.append(
                "Chirp end frequency {:.3f} GHz exceeds 64 GHz".format(end_frequency)
            )
        if not 0 < values.frequency_slope_mhz_us <= 266:
            issues.append("Frequency slope must be between 0 and 266 MHz/us")
        if not 2 <= values.adc_samples <= 2048:
            issues.append("ADC sample count must be between 2 and 2048")
        if not 100 <= values.sample_rate_ksps <= 12500:
            issues.append("Complex ADC sample rate must be between 0.1 and 12.5 Msps")
        if values.chirp_loops <= 0:
            issues.append("Chirp loops must be greater than 0")
        if values.frame_period_ms <= 0:
            issues.append("Frame period must be greater than 0")
        sample_rate_msps = values.sample_rate_ksps / 1000.0
        if sample_rate_msps > 0:
            required_ramp = values.adc_start_time_us + (
                values.adc_samples / sample_rate_msps
            )
            if values.ramp_end_time_us < required_ramp:
                issues.append(
                    "Ramp end time must be at least {:.2f} us to cover the ADC window".format(
                        required_ramp
                    )
                )
        if metrics.active_frame_time_ms > values.frame_period_ms:
            issues.append(
                "Frame period must be at least {:.3f} ms to hold all chirps".format(
                    metrics.active_frame_time_ms
                )
            )
        if metrics.bandwidth_mhz <= 0 or metrics.bandwidth_mhz >= 4000:
            issues.append("Effective sweep bandwidth must be between 0 and 4000 MHz")
        return tuple(issues)

    def render(self, values):
        issues = self.validate(values)
        if issues:
            raise ConfigValidationError(issues)

        template_lines = self.template_text().splitlines()
        body = self._replace_template_commands(template_lines, values)
        metrics = self.metrics(values)
        header = [
            "% IWR6843 raw ADC/LVDS configuration generated by RadarStream",
            "% Editable parameters are calculated by the integrated Python configurator",
            "% ADC samples/chirp: {} complex samples at {} ksps".format(
                values.adc_samples, values.sample_rate_ksps
            ),
            "% Sampled RF bandwidth: {:.3f} MHz".format(metrics.bandwidth_mhz),
            "% Chirp rate: {:.3f} Hz".format(metrics.chirp_rate_hz),
            "% Chirps/frame: {} chirps x {} loops = {}".format(
                metrics.tx_antennas,
                values.chirp_loops,
                metrics.tx_antennas * values.chirp_loops,
            ),
            "% Frame period: {} ms ({:.3f} FPS); active chirp time is {:.3f} ms".format(
                _format_number(values.frame_period_ms),
                metrics.frame_rate_hz,
                metrics.active_frame_time_ms,
            ),
            "",
        ]
        return "\n".join(header + body).rstrip() + "\n"

    def with_value(self, values, name, value):
        integer_fields = {
            "adc_samples",
            "sample_rate_ksps",
            "chirp_loops",
            "rx_channel_mask",
            "tx_channel_mask",
        }
        converted = int(round(value)) if name in integer_fields else float(value)
        return replace(values, **{name: converted})

    def _replace_template_commands(self, template_lines, values):
        output = []
        inserted_chirps = False
        for line in self._without_leading_comments(template_lines):
            tokens = line.split()
            command = tokens[0] if tokens else ""
            if command == "channelCfg":
                output.append(
                    "channelCfg {} {} 0".format(
                        values.rx_channel_mask, values.tx_channel_mask
                    )
                )
            elif command == "profileCfg":
                output.append(self._profile_line(tokens, values))
            elif command == "chirpCfg":
                if not inserted_chirps:
                    output.extend(self._chirp_lines(values))
                    inserted_chirps = True
            elif command == "frameCfg":
                tx_count = _bit_count(values.tx_channel_mask)
                tokens[1:3] = ["0", str(tx_count - 1)]
                tokens[3] = str(values.chirp_loops)
                tokens[5] = _format_number(values.frame_period_ms)
                output.append(" ".join(tokens))
            else:
                output.append(line)
        return output

    @staticmethod
    def _without_leading_comments(lines):
        body_started = False
        output = []
        for line in lines:
            stripped = line.strip()
            if not body_started and (not stripped or stripped.startswith(("%", "#"))):
                continue
            body_started = True
            output.append(line)
        return output

    @staticmethod
    def _profile_line(tokens, values):
        updated = list(tokens)
        updated[2] = _format_number(values.start_frequency_ghz)
        updated[3] = _format_number(values.idle_time_us)
        updated[4] = _format_number(values.adc_start_time_us)
        updated[5] = _format_number(values.ramp_end_time_us)
        updated[8] = _format_number(values.frequency_slope_mhz_us)
        updated[10] = str(values.adc_samples)
        updated[11] = str(values.sample_rate_ksps)
        return " ".join(updated)

    @staticmethod
    def _chirp_lines(values):
        masks = [
            1 << bit
            for bit in range(3)
            if values.tx_channel_mask & (1 << bit)
        ]
        return [
            "chirpCfg {0} {0} 0 0 0 0 0 {1}".format(index, mask)
            for index, mask in enumerate(masks)
        ]
