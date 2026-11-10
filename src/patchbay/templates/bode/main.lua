-- Bode plot: gain and phase of a circuit against frequency.
--
-- Wiring: generator CH1 -> circuit input and scope CH1,
--         circuit output -> scope CH2, all grounds together.
-- Set the sweep on the front panel. Each point is logged as frequency_Hz,
-- gain_dB and phase_deg (output relative to input).

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
local reference, previous, bracket

local function measure(hz)
    gen:set_frequency(1, hz)
    hz = gen:get_frequency(1)  -- what the generator really makes
    return hz, scope:measure_response(hz)
end

for i = 0, steps do
    -- log-spaced, rounded to 4 significant digits
    local hz, r = measure(tonumber(string.format("%.4g", start_hz * (stop_hz / start_hz) ^ (i / steps))))
    log { frequency_Hz = hz, gain_dB = r.gain_db, phase_deg = r.phase_deg }
    panel.frequency = hz
    panel.gain = r.gain_db
    panel.phase = r.phase_deg
    -- remember where the gain first crosses 3 dB below the first point
    reference = reference or r.gain_db
    local level = reference - 3
    if previous and not bracket and (previous.gain - level) * (r.gain_db - level) <= 0 then
        bracket = { low = previous.hz, high = hz, low_above = previous.gain > level }
    end
    previous = { hz = hz, gain = r.gain_db }
end

-- Measure the -3 dB point instead of reading it off between two points: halve
-- the interval (on a log scale) a few times. These points are not logged.
if bracket then
    local level = reference - 3
    for _ = 1, panel.refine_steps do
        local hz, r = measure(math.sqrt(bracket.low * bracket.high))
        if (r.gain_db > level) == bracket.low_above then bracket.low = hz else bracket.high = hz end
    end
    local corner = math.sqrt(bracket.low * bracket.high)
    panel.corner = corner
    print(string.format("-3 dB point: %.4g Hz (between %.4g and %.4g Hz; 3 dB below %.2f dB)",
        corner, bracket.low, bracket.high, reference))
else
    print("No -3 dB point in this range (3 dB below the first point)")
end

gen:set_output(1, false)
print("Sweep finished")
