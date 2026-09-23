# E1 Case-Aware Semantic Section Prototype

This prototype uses only existing Dambreak raw JSON content. It does not rerun treesitter-chunker or access upstream source files.

## Comparison for Dambreak.cpp::main()

| Policy | Chunks | Min B | Max B | Mean B | Median B | Oversized >5000 B |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| D3 | 3 | 2606 | 4658 | 3716.00 | 3884 | 0 |
| E1 | 14 | 31 | 4068 | 796.29 | 464.0 | 0 |

## Detected semantic section titles

- Build up an SPHSystem and IO environment.
- Creating bodies with corresponding materials and particles.
- Define body relation map. The contact map gives the topological connections between the bodies. Basically the the range of bodies to build neighbor particle lists. Generally, we first define all the inner relations, then the contact relations.
- Combined relations built from basic relations which is only used for update configuration.
- Define the numerical methods used in the simulation. Note that there may be data dependence on the sequence of constructions. Generally, the geometric models or simple objects without data dependencies, such as gravity, should be initiated first. Then the major physical particle dynamics model should be introduced. Finally, the auxiliary models such as time step estimator, initial condition, boundary condition and other constraints should be defined.
- Define the configuration related particles dynamics.
- Define the methods for I/O operations, observations and regression tests of the simulation.
- Prepare the simulation with cell linked list, configuration and case specified initial condition if necessary.
- Load restart file if necessary.
- Setup for time-stepping control
- Statistics for CPU time
- First output before the main loop.
- Main loop starts here.

- Sections subdivided for size: 0
- Malformed/ambiguous headers: 0
- Tree-sitter boundary conflicts: 0

E1 chunks are contiguous original substrings. Their boundaries start at human-authored separator-comment sections; oversized sections, if any, are subdivided only at top-level non-comment Tree-sitter statement/block boundaries.
