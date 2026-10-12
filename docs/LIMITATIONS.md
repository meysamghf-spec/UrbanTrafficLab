# Limitations

- There is no user authentication. Anyone who can reach the API can create studies and run simulations.
- SQLite is the default. PostgreSQL works through the connection string. PostGIS spatial indexes and spatial SQL are not used; geometry is GeoJSON.
- The synthetic Riverside grid is not a city. OSM import is optional and depends on Overpass. The studio does not require it.
- Signals, roundabouts, bridges, tunnels, and transit are simplified network attributes. They change the model that SUMO receives, and they are not detailed engineering designs.
- The estimator is not a SUMO run. Waiting time and queues are not estimated.
- Sample demand is synthetic. Results depend on that demand and on the documented assumptions.
- No observed-count validation or statistical test is included.
- SUMO output is usable only when the required XML files parse. A successful process with missing trip or summary files is a failure.
- Vehicle count is capped by `UTL_MAX_VEHICLES` (default 5000). Larger demand is rejected.
- The language model, when configured, may only propose operations. Validators decide whether they apply. The model cannot run shell commands.
- Map tiles are off by default. Supplying an OpenStreetMap tile URL requires you to follow that service's usage policy and show the attribution already requested in the map style.
- OSMnx is not a dependency. Road download uses an Overpass query.
- This repository does not record a completed production deployment.
