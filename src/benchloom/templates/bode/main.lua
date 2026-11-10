-- Bode plot: gain and phase of a circuit against frequency.
--
-- Wiring: generator CH1 -> circuit input and scope CH1,
--         circuit output -> scope CH2, all grounds together.
-- Set the sweep on the front panel. Each point is logged as frequency_Hz,
-- gain_dB and phase_deg (output relative to input), and with "Save waveforms"
-- on, the input and output waveforms of each point are saved as images in the
-- run's waveforms folder. With "Check the -3 dB point" on, the run passes only
-- if the -3 dB point lies within the limits (see the run's report.html).

local start_hz = panel.start_hz
local stop_hz = panel.stop_hz
local per_decade = panel.points_per_decade
assert(start_hz > 0 and stop_hz > start_hz, "the stop frequency must be above the start")

print("Generator: " .. gen:idn())
gen:set_signal(1, "sine", start_hz, panel.amplitude_vpp, 0)
gen:set_output(1, true)
for channel = 1, 2 do
    scope:set_coupling(channel, "DC")  -- AC coupling would add its own phase shift
    scope:set_position(channel, 0)
end

local steps = math.max(1, math.floor(math.log(stop_hz / start_hz, 10) * per_decade + 0.5))
local points = {}

local function frequency_text(hz)  -- 31190 -> "31.19 kHz"
    if hz >= 1e6 then return string.format("%.4g MHz", hz / 1e6) end
    if hz >= 1e3 then return string.format("%.4g kHz", hz / 1e3) end
    return string.format("%.4g Hz", hz)
end

local function measure(hz)
    gen:set_frequency(1, hz)
    hz = gen:get_frequency(1)  -- what the generator really makes
    return hz, scope:measure_response(hz)
end

for i = 0, steps do
    -- log-spaced, rounded to 4 significant digits
    local hz, r = measure(tonumber(string.format("%.4g", start_hz * (stop_hz / start_hz) ^ (i / steps))))
    log { frequency_Hz = hz, gain_dB = r.gain_db, phase_deg = r.phase_deg }
    if panel.save_waveforms then
        save_waveform(frequency_text(hz), { input = r.input, output = r.output })
    end
    panel.frequency = hz
    panel.gain = r.gain_db
    panel.phase = r.phase_deg
    points[#points + 1] = { hz = hz, gain = r.gain_db }
end

-- The -3 dB points: where the gain falls 3 dB below its maximum, on either side
-- of the maximum (a low-pass has only the upper one, a high-pass only the lower
-- one, a band-pass both). Each is measured, not read off between two points:
-- the interval is halved (on a log scale) a few times. These are not logged.
local peak = 1
for i, p in ipairs(points) do
    if p.gain > points[peak].gain then peak = i end
end
local level = points[peak].gain - 3

local function refine(above, below)  -- frequencies with the gain above / below the level
    for _ = 1, panel.refine_steps do
        local hz, r = measure(math.sqrt(above * below))
        if r.gain_db > level then above = hz else below = hz end
    end
    return math.sqrt(above * below)
end

local lower, upper
for i = peak - 1, 1, -1 do  -- going down from the maximum
    if points[i].gain <= level then
        lower = refine(points[i + 1].hz, points[i].hz)
        panel.corner_low = lower
        print(string.format("Lower -3 dB point: %.4g Hz", lower))
        break
    end
end
for i = peak + 1, #points do  -- going up from the maximum
    if points[i].gain <= level then
        upper = refine(points[i - 1].hz, points[i].hz)
        panel.corner_high = upper
        print(string.format("Upper -3 dB point: %.4g Hz", upper))
        break
    end
end
print(string.format("Maximum gain: %.2f dB at %.4g Hz", points[peak].gain, points[peak].hz))
if not (lower or upper) then
    print("No -3 dB point in this range")
end

if panel.check_limits then
    -- the upper point of a low-pass or band-pass, the lower one of a high-pass;
    -- no point at all fails the check
    check("-3 dB point", upper or lower or false, panel.limit_low_hz, panel.limit_high_hz, "Hz")
end

gen:set_output(1, false)
print("Sweep finished")
