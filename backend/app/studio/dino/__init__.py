"""Built during the Meta Global AI Developer Hackathon (new work). Existing model, new integration:
DINOv2 (facebook/dinov2-base, Apache 2.0) existed before the hackathon and is used unmodified. New here: a Modal
function, two tables, a visual-change curve, a render-integrity check, own-reel matching and audio attention curves.

This package is isolated on purpose (new files only, one router include, one config block): it imports nothing from
the edit engine or the ig_* models. Nothing here invents numbers: when the model service is off or fails, callers get
DinoUnavailable and report available=false.
"""
