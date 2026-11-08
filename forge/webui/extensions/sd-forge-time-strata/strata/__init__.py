"""Time Strata: the frames of a moving figure, cut out and stood one behind the other in a 3D world, so that
the figure becomes a solid chain through time that the camera flies along, into and through ("video strata",
after David Daniels' Strata-Cut).

The page (javascript/strata-*.js) is the whole program the user sees: the world, the chains, the camera and
the recorder are drawn there with WebGL. This package is what it talks to:

    store     projects, chains and their files on disk
    jobs      the queue of slow work (cutting frames out, generating with Deforum)
    media     reading videos and pictures
    cut       the cut: background removal, clean alpha (no fog in a stack), colours without a fringe, alignment
    chain     a source (video, pictures, Deforum's frames) made into a chain of cut, aligned frames
    deforum   generating a chain's frames with Deforum, set up for the shot
    export    the recorded flight, encoded as a video
    api       the routes under /time-strata
"""

NAME = "Time Strata"
FORMAT = "time-strata"
VERSION = 1
