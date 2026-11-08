"""Simulation 360: worlds made of 360 panoramas that the user walks between.

The page (javascript/sim360-*.js) is the whole program the user sees. This package is what it talks to:

    store     projects, worlds and their files on disk
    tile      "Tile X": a picture whose left edge continues its right edge
    runner    drawing with the loaded checkpoint (a world, Hires. fix, a character inside a view)
    depth     how far each direction of a panorama is, so the camera can move inside it
    segment   automatic selection of the thing the user clicked on
    export    the recorded flight, encoded as a video
    api       the routes under /sim360
"""

NAME = "Simulation 360"
FORMAT = "simulation-360"
VERSION = 1
