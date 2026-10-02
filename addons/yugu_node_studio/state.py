"""Composer membership belongs to this extension and this Scene."""


def active_avatar(scene):
    avatars = getattr(scene, "yugus_avatars", ())
    index = int(getattr(scene, "yugus_avatar_index", -1))
    return avatars[index] if 0 <= index < len(avatars) else None
