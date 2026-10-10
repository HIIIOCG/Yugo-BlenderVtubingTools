"""SMPL rest topology and FK, independent of any physics engine (WXYZ)."""
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
from . import tracker_math as tm

ASSETS = Path(__file__).parent / 'assets'

class Skeleton:
    def __init__(self):
        root = ET.parse(ASSETS / 'smpl_humanoid.xml').getroot().find('worldbody/body')
        self.names, self.parents, self.offsets, self.rest_rot, self.geoms = [], [], [], [], []
        def visit(body, parent):
            idx = len(self.names)
            self.names.append(body.get('name'))
            self.parents.append(parent)
            self.offsets.append(list(map(float, body.get('pos', '0 0 0').split())))
            self.rest_rot.append(list(map(float, body.get('quat', '1 0 0 0').split())))
            for geom in body.findall('geom'):
                self.geoms.append((idx, dict(geom.attrib)))
            for child in body.findall('body'):
                visit(child, idx)
        visit(root, -1)
        self.offsets = np.asarray(self.offsets)
        self.rest_rot = np.asarray(self.rest_rot)
        if len(self.names) != 24 or np.max(np.abs(self.rest_rot-[1,0,0,0])) > 1e-7:
            raise ValueError('Unsupported SMPL asset topology')

    def fk(self, root_pos=(0,0,.95), root_rot=(1,0,0,0), local=None):
        local = np.tile([1.,0,0,0], (24,1)) if local is None else np.asarray(local)
        pos, rot = np.empty((24,3)), np.empty((24,4))
        pos[0], rot[0] = root_pos, root_rot
        for i in range(1,24):
            parent = self.parents[i]
            pos[i] = pos[parent] + tm.rotate(rot[parent], self.offsets[i])
            rot[i] = tm.multiply(rot[parent], tm.multiply(self.rest_rot[i], local[i]))
        return pos, rot
