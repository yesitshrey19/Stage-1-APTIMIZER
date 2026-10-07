"""Rule-book floor-plan engine for one residential dwelling unit.

The package exists because the previous packer (``backend/vastu.py``) generated geometry
and then labelled it: it guillotine-sliced one full-depth column per habitable room, so on
a 6.6 m-deep flat every "room" was a 2.6-3.0 m column at aspect 2.2-2.5 — a hard reject for
every room type under rule book 5.1. The order is what is being replaced, not the maths:

    1 validate envelope -> 2 assign facade budget -> 3 fix entry point -> 4 zone ->
    5 wet core -> 6 circulation spine -> 7 grow rooms from facade inward ->
    8 doors -> 9 windows -> 10 hard validate -> 11 soft score / GA

Topology is frozen before any geometry exists, and the genetic search tunes dimensions
only — never room count, the adjacency graph, the entrance, or zone assignment.

This file is deliberately a bare marker for now. The public API (``generate_unit_plan``
and the metre-unit payload the renderer consumes) lands in the integration phase, once
every submodule exists. Importing submodules from here today would create import cycles
while spec.py's dependents are still being written, and a cycle discovered at that point
costs more than the convenience is worth. Import the submodule you need directly:

    from floorplan.spec import RectMM, RoomType

``backend/`` is the import root — modules do ``import vastu``, not ``from backend import
vastu`` — so this package is reached as ``floorplan.<module>``.
"""
