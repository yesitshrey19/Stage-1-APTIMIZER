// Stage 1 (Site) prototype catalogue. Shared by navigation and the public directory.
// Parameter labels reflect the actual module inputs/outputs; the directory is a curated index, not certification.
import { Map, Globe2, Landmark, FileText } from "lucide-react";

const input = (name, unit, basis) => ({ name, unit, basis, kind: "Input" });
const computed = (name, unit, basis) => ({ name, unit, basis, kind: "Calculated" });
const estimate = (name, unit, basis) => ({ name, unit, basis, kind: "Estimate" });
export const WORKSPACE_CATALOG = [
  { id: "site", label: "Site", icon: Map, modules: [
    { id: "plot", name: "Plot & Setbacks", icon: Map, note: "Boundary geometry and development controls.", parameters: [
      input("Plot boundary coordinates", "lat / lng", "Drawn or edited vertices; a new project's boundary is a placeholder."),
      input("Surveyed land boundary points", "point name / lat / lng", "Optional surveyed boundary points from the survey sketch applied directly as the plot polygon."),
      input("Plot length & width", "m", "Editable rectangular plot dimensions when polygon geometry is not used."),
      input("Plot orientation", "° from North", "Site orientation input."),
      input("Plot reference", "text", "Survey or plot reference identifying the project."),
      input("Road-facing edge index", "edge index", "Polygon boundary edge index marked as abutting a public road alongside its road width."),
      input("Road-facing edge widths", "m", "Width recorded per boundary edge; the widest road edge sets the front of the plot."),
      input("Front, rear & side setbacks", "m", "Project development controls; confirm local byelaws."),
      input("Default (unclassified edge) setback", "m", "Applied to boundary edges not marked road-facing."),
      input("Access ring & driveway width", "m", "Site circulation inputs."),
      input("Maximum distance to road", "m", "Tower placement constraint."),
      input("Maximum residential towers", "count", "Blank allows the layout engine to determine what fits."),
      input("Minimum & maximum floors", "floors", "Tower layout bounds."),
      computed("Plot area & buildable envelope", "m²", "Local planar polygon measurement; not a cadastral survey."),
      computed("Plot area in acres & vertex count", "acres / vertices", "Plot area converted to acres (m² ÷ 4,046.86) and active polygon vertex count."),
      computed("Envelope share of plot", "% of plot", "Envelope area against total plot area, with the envelope region count."),
      computed("Per-edge setback classification, length & NBC Part 3 minimum", "class / m", "Boundary edge classification (front, rear, side), measured length, and NBC 2016 Part 3 height-based minimum setback check."),
      computed("Reserved roads, amenities & packable land", "m² / %", "Setbacks, circulation and amenity reservations subtracted from the envelope before packing."),
      computed("Packed towers, units & achieved FAR", "count / units / ratio", "Site layout engine result; each constraint is reported as satisfied or violated."),
      computed("Total building footprint & buildable floor area", "m²", "Sum of packed tower footprints and total buildable floor area across all packed towers."),
      computed("Ground coverage & open space", "%", "Measured from the packed layout, not assumed."),
      computed("Community green / play area", "m²", "Dedicated community open green and play zone reserved inside the setback envelope."),
      computed("Amenity block dimensions, floors & height", "m / floors / m", "Footprint area, width × depth, floor count, and building height for each reserved society amenity block."),
      computed("Pedestrian access links", "m", "Pedestrian circulation links and lengths connecting towers, amenities, and internal access roads."),
      computed("Recommended development controls", "m / ratio / %", "Setbacks, FAR cap and coverage cap derived from plot area and abutting road width; FAR and coverage are indicative pending the local DCR."),
      estimate("Recommended building height, floors & dwelling units", "m / floors / units", "Preliminary building height, floor count, and dwelling unit yield recommended from plot area and widest abutting road width."),
      computed("Genetic refinement run", "generations / score", "Off-grid search telemetry when the layout engine refines the greedy packing."),
      computed("Genetic refinement evaluations, fitness score & NBC spacing check", "evaluations / fitness / status", "Off-grid genetic algorithm evaluation count, seed vs. final fitness score, tower count delta, and NBC Part 3 inter-building clearance verification."),
    ] },
    { id: "gis", name: "GIS Intelligence", icon: Globe2, note: "Terrain, surroundings and environmental context.", parameters: [
      input("Study radius", "m", "Surroundings query radius."),
      estimate("Elevation, slope & flood context", "m / %", "Depends on external data coverage and resolution."),
      computed("Sun path", "°", "Location and time-based solar geometry."),
      estimate("Rooftop solar yield", "kWh / year", "Roof area, regional insolation and system assumptions."),
      computed("Terrain min / max / mean / relief", "m", "Elevation samples within the study radius."),
      computed("Diagonal elevation profile & surrounding ring mean", "m", "Elevation profile sampled across the plot diagonal horizontal run and mean elevation of surrounding ground ring."),
      computed("Flood design response", "m", "Nearest water body, elevation delta and minimum plinth height above road level."),
      estimate("Flood risk level & score", "level / /100", "Composite flood hazard level (Low, Moderate, High) and score derived from water proximity and elevation delta."),
      computed("Seismic zone, site class & PGA", "zone / class / g", "City reference with site class from the selected soil; PGA reported for rock and surface."),
      computed("IS 875-3 design wind", "m/s / kN/m²", "Basic speed, terrain-2 design speed and pressure at the design height."),
      estimate("Prevailing & summer wind directions and mean speed", "direction / m/s", "Regional prevailing and summer wind directions, mean wind speed, and natural cross-ventilation orientation guidance."),
      computed("Suitability score & grade", "/100", "Weighted site assessment; a screening aid, not a geotechnical report."),
      computed("Suitability factor breakdown & weights", "score / %", "Individual factor scores, percentage weights, and weighted contributions for slope, flood risk, road access, and surroundings."),
      computed("Buildability constraint flags", "critical / warning / ok", "Automated site buildability status and severity-graded engineering constraint flags."),
      computed("Accessibility & surroundings counts", "m / count", "Nearest road distance and nearby building features from public data."),
      computed("Accessibility score & transit notes", "/100", "Site accessibility score and proximity notes synthesized from detected roads and transit stops."),
      computed("Facade orientation bearings, equinox sun hours & shading guidance", "° / hours / text", "Per-facade compass bearing, equinox direct sun exposure hours, and orientation-specific shading recommendations."),
      computed("Solar system size, saving & payback", "kWp / kWh / INR", "Array size, yearly generation, tariff saving and simple payback."),
      estimate("Usable terrace area, solar insolation & CO₂ avoided", "m² / kWh/m²/yr / tCO₂/yr", "Net usable roof area after core/tank deductions, annual and monthly solar insolation profile, specific yield, and annual CO₂ emissions avoided."),
    ] },
    { id: "township", name: "Township & Master Plan", icon: Landmark, note: "Parcel zoning and mixed-use planning.", parameters: [
      input("Township area", "m²", "Total parcel area the master plan is generated over."),
      input("Mixed-use zoning", "on / off", "Enables retail podium and commercial galleria allocation."),
      input("Parcel allocation", "%", "Land-use distribution across the township."),
      computed("Zoning & circulation", "m²", "Generated master-plan geometry; requires planning review."),
      computed("Land-use shares & permissible FSI", "% / m² / FSI", "Zone-by-zone distribution with the FSI each zone is planned at."),
      computed("Sector breakdown", "units / towers / GLA", "Phased development clusters and their community hubs."),
      computed("Internal right of way", "m", "Internal roadway width in the circulation hierarchy."),
      computed("Pedestrian segregation & gate hierarchy strategy", "strategy", "Master-plan circulation mobility strategy defining pedestrian-vehicular loop segregation, gate hierarchy, dedicated cycle track, and tree verge."),
      estimate("Dwellings, GLA & blended FAR", "units / m² / ratio", "Indicative master-plan yield; align with the statutory development plan."),
      estimate("Township area in acres & estimated population", "acres / persons", "Total township parcel in acres (m² ÷ 4,046.86) and estimated resident population from dwelling unit yield."),
      estimate("Total potential built-up area & open space ratio", "m² / %", "Master-plan total potential built-up area at blended FAR and open space ratio reserved for parks and recreation."),
    ] },
    { id: "reports", name: "Site Reports", icon: FileText, note: "Ready-to-export PDF reports for the site stage.", parameters: [
      computed("Plot & Setbacks report", "PDF", "Plot geometry, road-facing edges and per-edge setback check against the NBC 2016 Part 3 minimum."),
      computed("Site Layout & Massing report", "PDF", "Land budget, packed towers, achieved FAR, coverage, roads, amenities and green space."),
      computed("Site Analysis report", "PDF", "Suitability, buildability, terrain, flood, seismic, wind, sun path, solar and surrounding context."),
      computed("Township & Master Plan report", "PDF", "Land-use zoning, sectors, dwelling and population yield, and circulation strategy."),
      computed("Site stage report set", "PDF", "All site reports merged into one document behind a contents page."),
    ] },
  ] },
];
export const WORKSPACE_MODULES = WORKSPACE_CATALOG.flatMap(group => group.modules);
export const PARAMETER_COUNT = WORKSPACE_MODULES.reduce((total, module) => total + module.parameters.length, 0);
