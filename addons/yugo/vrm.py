"""Small, dependency-free helpers for reading VRM Add-on metadata."""


def get_vrm_extension(armature_object):
    if armature_object is None or armature_object.type != "ARMATURE":
        return None
    return getattr(armature_object.data, "vrm_addon_extension", None)


def is_vrm_armature(armature_object):
    return get_vrm_extension(armature_object) is not None


def get_avatar_name(armature_object):
    extension = get_vrm_extension(armature_object)
    if extension is None:
        return armature_object.name if armature_object else "No avatar selected"

    spec_version = str(getattr(extension, "spec_version", ""))
    if spec_version.startswith("1"):
        meta = getattr(getattr(extension, "vrm1", None), "meta", None)
        name = getattr(meta, "name", "")
    else:
        meta = getattr(getattr(extension, "vrm0", None), "meta", None)
        name = getattr(meta, "title", "")

    return name or armature_object.name


def get_avatar_thumbnail(armature_object):
    extension = get_vrm_extension(armature_object)
    if extension is None:
        return None

    spec_version = str(getattr(extension, "spec_version", ""))
    if spec_version.startswith("1"):
        meta = getattr(getattr(extension, "vrm1", None), "meta", None)
        return getattr(meta, "thumbnail_image", None)

    meta = getattr(getattr(extension, "vrm0", None), "meta", None)
    texture = getattr(meta, "texture", None)
    if texture is not None:
        return texture
    return getattr(meta, "thumbnail_image", None)
