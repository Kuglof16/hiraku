"""K2: 3D vehicle physics, solved directly in time with numpy.

Sprung body (6 DOF) + per-corner travel along K&C paths + wheel spin + tyre
deflection states + road. No physics-engine joints. See sim.py for the
equations, checks.py for the checks that must pass before any scenario runs.
"""
