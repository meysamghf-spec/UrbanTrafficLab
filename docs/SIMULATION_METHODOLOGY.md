# Simulation methodology

Simulation outputs are decision-support results. They are not guaranteed predictions of real-world traffic. Road geometry alone does not determine demand, and this project does not claim calibration against observed counts.

## Network conversion

SUMO nodes are projected from the study bbox with an equirectangular scale (111320 m per degree of longitude at the origin latitude, 110540 m per degree of latitude). Speeds are converted from km/h to m/s. Edge length is the haversine length stored on the edge.

Node type is `traffic_light` when a signal is present, `right_before_left` for a roundabout, and `priority` otherwise. SUMO's default traffic-light program is used. Cycle times stored on the signal are not compiled into a custom signal plan. Roundabout geometry, bridge structure, and tunnel cross-section are simplified: the operation changes connectivity, speed caps, or edge parameters and records a `simplified_model` warning.

Routes are explicit shortest paths plus `flow` elements. `duarouter` is not used. Car and bus vehicle types use `sigma` 0.5. Teleporting is disabled (`--time-to-teleport -1`). Collisions are warnings. The simulation end is the demand horizon plus `UTL_SIMULATION_END_BUFFER_S` (default 900 seconds) so vehicles that depart near the end of the hour can finish. Edge data is requested every 600 seconds.

A process exit of 0 is not enough. The run is `SUCCEEDED` only when tripinfo and summary outputs parse. Link coloring is `full` usability when edge data also parses, otherwise `partial`.

## Demand

Sample morning demand uses seed 42 and 20 synthetic car trips over 3600 seconds. Evening demand uses seed 43. The Crosstown bus is a simplified vehicle flow at a 15-minute headway, without passengers or dwell. If a development adds housing units, generated trips are synthetic, change the demand fingerprint, and make that scenario incomparable with a baseline run that did not include them.

## Estimator

The optional estimator is a static bureau-of-public-roads assignment:

- alpha 0.15, beta 4
- 1600 vehicles per hour per lane, uncalibrated
- 4 method-of-successive-averages iterations

Waiting time and queue length are omitted, not reported as zero. Repeats are deterministic, so their standard deviation is zero. Quality is `ESTIMATED`.

## Metrics

| Key | Meaning | Unit |
| --- | --- | --- |
| `completed_trips` | Trips with a non-negative arrival | trips |
| `departed_vehicles` | Vehicles inserted, from the SUMO summary when present | vehicles |
| `arrived_vehicles` | Vehicles arrived | vehicles |
| `unfinished_trips` | Inserted minus arrived, plus trips with no path | trips |
| `unroutable_trips` | Demand with no directed path | trips |
| `mean_travel_time_s` | Mean duration of completed trips | s |
| `mean_speed_kmh` | Unweighted mean of completed-trip length / duration | km/h |
| `mean_time_loss_s` | Mean SUMO time loss of completed trips, or free-flow delay for the estimator | s |
| `mean_waiting_time_s` | Mean SUMO waiting time; missing for the estimator | s |
| `network_throughput_vph` | Arrived vehicles divided by the demand horizon in hours | veh/h |

Missing values stay missing. A zero baseline makes the relative difference undefined. No statistical significance test is computed. Runs are comparable only when engine, seed, repeats, horizon, simulation end, demand fingerprint, transit fingerprint, study area, and result quality match.

## Validation

There is no comparison against observed traffic counts in this repository. Using the results for a real city requires a reviewed network, demand built from counts or a documented survey, and a validation exercise that this software does not perform automatically.
