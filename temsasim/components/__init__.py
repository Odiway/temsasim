"""Physical component models.

Each module here owns one piece of the bus and knows nothing about the solver
loop. They are plain dataclasses built from the YAML library by
``temsasim.library``, so a TEMSA engineer adds a new motor or pack by adding a
file, never by editing Python.
"""
