"""Every tunable number for the site layout engine lives here.

No geometry or optimisation module hardcodes a distance, ratio or limit — they all read
it off `SiteLayoutConfig`. `SiteLayoutConfig.from_dict` deep-merges a partial override
(what the API receives from the client) onto the defaults, so callers only send what they
actually changed.
"""
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SetbackConfig:
    """Perimeter setbacks in metres, measured inward from the plot boundary.

    `front` / `rear` / `side` fall back to `default` when left as None. Front edges are
    the plot edges flagged road-facing in `plot.road_edges`; the rear is the edge whose
    outward normal most opposes the mean front normal; everything else is a side.
    """
    default: float = 6.0
    front: Optional[float] = None
    rear: Optional[float] = None
    side: Optional[float] = None

    def for_class(self, edge_class: str) -> float:
        value = {"front": self.front, "rear": self.rear, "side": self.side}.get(edge_class)
        return float(self.default) if value is None else float(value)


@dataclass
class RoadConfig:
    """Circulation reserved inside the envelope before any tower is placed.

    Every corridor is a straight rectangle cut from a straight-sided development block —
    see `siteplan/blocks.py` for why a road that follows the plot boundary is the wrong
    answer even when it is geometrically valid.
    """
    enabled: bool = True
    ring_width: float = 6.0          # perimeter access ring — NBC fire-tender minimum
    ring_offset: float = 0.0         # gap between envelope edge and outer face of the ring
    driveway_width: float = 4.5      # internal spurs linking clusters to the ring
    max_distance_to_road: float = 45.0   # a tower further than this counts as unreachable
    # A core deep enough for two rows of flats gets one central drive even when every
    # point of it is already within reach of the ring: it is what the surface bays line,
    # and without it the middle of the block is a field no car can stop in. 0 disables.
    central_spine_min_core: float = 45.0
    # An irregular plot cannot be covered by one rectangle without abandoning land, so the
    # envelope is tiled with up to this many straight-sided blocks, largest first.
    max_blocks: int = 3
    min_block_area: float = 900.0    # below this a leftover rectangle is landscape, not a block


@dataclass
class AmenityBlock:
    """A standalone community footprint — never part of a tower, own height parameter.

    Size comes from exactly one of `dimensions` (explicit w x d in metres), `area_sqm`
    (explicit footprint area, packed to `aspect`), or `plot_area_pct` (a share of the
    plot). Resolution order is dimensions -> area_sqm -> plot_area_pct.

    `programme` distributes the facilities over the block's storeys. Scattering a gym, a
    pool and a clubhouse across the site as three separate single-storey pavilions costs
    three footprints, three sets of services and three approach roads for the same
    accommodation one stacked building provides — and every square metre of ground it
    saves is open space the flats get back. So the facilities are levels of one building,
    and this is the list of them.
    """
    key: str
    name: str
    height_m: float = 4.5
    floors: int = 1
    dimensions: Optional[List[float]] = None
    area_sqm: Optional[float] = None
    plot_area_pct: Optional[float] = None
    aspect: float = 1.6              # w:d used when only an area is given
    # Clamps applied after sizing. A percentage of plot area is the right shape of
    # rule for a small scheme and absurd for a township — 3% of 6 ha is a 1.8 ha
    # clubhouse — so the percentage sets the intent and these set the sane range.
    min_area_sqm: Optional[float] = None
    max_area_sqm: Optional[float] = None
    # [{level, name, facilities: [...], open_air: bool}] — level 0 is the ground floor and
    # level == floors is the roof. Plain dicts so `asdict` round-trips through the API.
    programme: Optional[List[Dict[str, Any]]] = None


def _clubhouse_programme() -> List[Dict[str, Any]]:
    """Facilities of the integrated clubhouse, ground up. Roof carries the pool."""
    return [
        {"level": 0, "name": "Ground floor",
         "facilities": ["Entrance lobby & reception", "Multipurpose community hall",
                        "Creche & toddlers' room", "Convenience store", "Society office"],
         "open_air": False},
        {"level": 1, "name": "First floor",
         "facilities": ["Gymnasium", "Aerobics & yoga studio", "Changing rooms",
                        "Physiotherapy / spa"],
         "open_air": False},
        {"level": 2, "name": "Second floor",
         "facilities": ["Indoor games — table tennis, billiards, carrom", "Library & reading room",
                        "Co-working lounge", "Home theatre"],
         "open_air": False},
        {"level": 3, "name": "Third floor",
         "facilities": ["Banquet hall & party lawn deck", "Cafe & pantry", "Guest suites"],
         "open_air": False},
        {"level": 4, "name": "Roof terrace",
         "facilities": ["Swimming pool", "Kids' splash pool", "Pool deck & sun loungers",
                        "Open-air barbecue"],
         "open_air": True},
    ]


def _default_amenities() -> List[AmenityBlock]:
    """One integrated clubhouse rather than a scatter of single-purpose pavilions."""
    return [
        AmenityBlock("clubhouse", "Integrated Community Clubhouse",
                     height_m=14.0, floors=4, plot_area_pct=3.0, aspect=1.6,
                     min_area_sqm=240.0, max_area_sqm=1200.0,
                     programme=_clubhouse_programme()),
    ]


@dataclass
class AmenityConfig:
    enabled: bool = True
    blocks: List[AmenityBlock] = field(default_factory=_default_amenities)
    total_cap_pct: float = 8.0       # amenity footprints never exceed this % of plot area
    clearance: float = 3.0           # gap kept between an amenity and anything else

    # Placement scoring. Each term is normalised to 0..1 and combined as a weighted sum,
    # so every term influences the choice rather than being hidden by a lexicographic key.
    #   compactness — leaves the largest single block of packable land (least fragmenting)
    #   spread      — keeps multiple amenities apart
    #   road        — stays accessible from the circulation network
    #   centrality  — avoids pinning the clubhouse to a parcel corner
    compactness_weight: float = 0.28
    spread_weight: float = 0.18
    road_weight: float = 0.14
    centrality_weight: float = 0.28
    green_weight: float = 0.12
    # Separation at which the spread term is fully satisfied. 0 derives it from the size
    # of the packable region, which is what makes it scale from a plot to a township.
    target_separation: float = 0.0
    separation_scale: float = 0.55   # multiplier on sqrt(region area) when auto-deriving


@dataclass
class TowerConfig:
    """Search space for tower footprints during packing.

    Residential blocks are slabs, not cubes: a real apartment building is a long bar one
    or two units deep so every flat gets a facade for light and cross ventilation. Square
    footprints land habitable rooms in the middle of the plate with no external wall, and
    they are what made the generated site read as a cluster of office boxes rather than a
    housing scheme. The aspect band below enforces the bar proportion.
    """
    candidate_widths: List[float] = field(default_factory=lambda: [36.0, 45.0, 54.0, 63.0, 72.0])
    candidate_depths: List[float] = field(default_factory=lambda: [13.0, 15.0, 17.0, 19.0])
    rotations_deg: List[float] = field(default_factory=lambda: [0.0, 15.0, 30.0, 45.0, 60.0, 75.0])
    # Long side : short side. Below ~2 the block stops reading as a residential bar;
    # above ~6 the corridor runs get impractical and the plate is hard to serve from one core.
    min_aspect: float = 2.2
    max_aspect: float = 5.5
    min_footprint: float = 400.0
    max_footprint: float = 1400.0
    floors_min: int = 4
    floors_max: int = 24
    floor_height: float = 3.0
    # Cap on how many residential towers the layout may contain. None = as many as the
    # land, FAR and spacing rules allow. When the cap bites, the lowest-yield towers are
    # dropped first so the retained ones are the most productive.
    max_towers: Optional[int] = None
    # Light and ventilation: clear gap between two towers must be at least
    # max(spacing_min, spacing_height_factor x mean height of the pair).
    spacing_min: float = 6.0
    spacing_height_factor: float = 0.5
    carpet_efficiency: float = 0.78  # footprint -> saleable carpet, for unit counting
    area_per_unit: float = 95.0      # mean carpet area per dwelling unit


@dataclass
class SurfaceParkingConfig:
    """Perpendicular (90-degree) surface bays flanking the drives — SP:21 dimensions."""
    enabled: bool = True
    stall_width: float = 2.5
    stall_depth: float = 5.0
    kerb_offset: float = 0.3     # gap between the carriageway edge and the stall nose


@dataclass
class OpenSpaceConfig:
    """Landscaped open space kept out of the packable region.

    A community green is what stops a scheme reading as buildings-and-tarmac. It is
    reserved before tower packing so it survives, rather than being whatever land the
    packer failed to use.
    """
    enabled: bool = True
    green_pct_of_plot: float = 8.0   # target size of the central green
    min_area: float = 150.0          # below this a green is not worth reserving
    clearance: float = 3.0


@dataclass
class GaConfig:
    enabled: bool = True
    population: int = 60
    generations: int = 80
    mutation_rate: float = 0.15
    crossover_rate: float = 0.75
    elite: int = 4
    seed: Optional[int] = None
    # Wall-clock ceiling on the refinement, checked between generations. Population and
    # generations bound the work in the abstract; this bounds it in a request, where a
    # large plot with many candidate towers would otherwise make the two above mean a
    # very different amount of time than they do on a small one.
    time_budget_s: float = 2.0


@dataclass
class SiteLayoutConfig:
    setbacks: SetbackConfig = field(default_factory=SetbackConfig)
    road: RoadConfig = field(default_factory=RoadConfig)
    amenities: AmenityConfig = field(default_factory=AmenityConfig)
    towers: TowerConfig = field(default_factory=TowerConfig)
    parking: SurfaceParkingConfig = field(default_factory=SurfaceParkingConfig)
    open_space: OpenSpaceConfig = field(default_factory=OpenSpaceConfig)
    ga: GaConfig = field(default_factory=GaConfig)

    far_cap: float = 3.0
    ground_coverage_cap_pct: float = 40.0
    min_region_area: float = 25.0        # discard envelope/residual slivers below this
    # Offsets are computed with a polygonal approximation of the round offset, which
    # under-shoots the true distance by a fraction of a percent, and `simplify` (when
    # enabled) may push the boundary outward by up to its own tolerance. Both erode the
    # setback. `setback_safety` is added to every offset so the delivered envelope always
    # clears the nominal setback rather than falling a few centimetres short of it.
    setback_safety: float = 0.02
    simplify_tolerance: float = 0.0      # metres; > 0 trades exactness for fewer vertices
    buffer_quad_segs: int = 16           # arc resolution on offsets
    fast_preview: bool = False           # short-circuit to greedy, skip GA refinement

    # ---------------------------------------------------------------- overrides
    _SECTIONS = {
        "setbacks": SetbackConfig,
        "road": RoadConfig,
        "amenities": AmenityConfig,
        "towers": TowerConfig,
        "parking": SurfaceParkingConfig,
        "open_space": OpenSpaceConfig,
        "ga": GaConfig,
    }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]] = None) -> "SiteLayoutConfig":
        """Deep-merge a partial override onto the defaults. Unknown keys are ignored so a
        stale client cannot 500 the endpoint."""
        data = dict(data or {})
        kwargs: Dict[str, Any] = {}

        for name, section_cls in cls._SECTIONS.items():
            payload = data.get(name)
            if not isinstance(payload, dict):
                continue
            if name == "amenities" and isinstance(payload.get("blocks"), list):
                payload = dict(payload)
                payload["blocks"] = [
                    AmenityBlock(**{k: v for k, v in b.items()
                                    if k in AmenityBlock.__dataclass_fields__})
                    for b in payload["blocks"] if isinstance(b, dict) and b.get("key")
                ]
            kwargs[name] = section_cls(**{k: v for k, v in payload.items()
                                          if k in section_cls.__dataclass_fields__})

        scalar_fields = set(cls.__dataclass_fields__) - set(cls._SECTIONS)
        for key in scalar_fields:
            if key in data:
                kwargs[key] = data[key]

        return cls(**kwargs)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
