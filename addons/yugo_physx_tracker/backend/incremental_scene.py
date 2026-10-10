"""Incremental native scene entities, without reattaching the SMPL stage.

Each entity is a USD reference at its own child path. Editing a reference on
World itself would resync the whole articulation, so that is never used here.
Only changed bodies/chains are replaced; unchanged native actors keep moving.
"""
import copy
import hashlib
import json

import scene_inputs as author


EMPTY_SOURCE = '#usda 1.0\ndef Xform "World"\n{\n}\n'


class FatalSceneError(RuntimeError):
    """A native mutation failed after committing; stop rather than mask it."""
    fatal = True


def normalize(config):
    return author.append_scene_inputs(EMPTY_SOURCE,config)[1]


def _body_fingerprint(item):
    keys = ('id','mode','shape','mass','friction','restitution','linear_damping',
            'angular_damping','contact_offset','axis','size','radius','height',
            'collider_center','lock_location_mask','lock_rotation_mask','points',
            'triangles','anchor_body','anchor_local_pose','spring_collider')
    result = {key:item[key] for key in keys if key in item}
    if item.get('spring_link'):
        result.update({key:item.get(key) for key in ('parent_id','length','stiffness',
                      'drive_damping','gravity')})
    return result


def _groups(spec):
    result = {}
    for field in ('objects','colliders'):
        for item in spec[field]:
            result[('body',item['id'])] = {'kind':'body','field':field,'item':item,'items':[item]}
    for spring in spec['springs']:
        result[('spring',spring['id'])] = {'kind':'spring','spring':spring,
                                         'items':[spring['anchor'],*spring['links']]}
    for key,group in result.items():
        if group['kind']=='body':
            physical = _body_fingerprint(group['item'])
        else:
            spring = group['spring']
            physical = {'anchor_body':spring.get('anchor_body'),
                        'anchor_local_pose':spring['anchor_local_pose'],
                        'links':[_body_fingerprint(link) for link in spring['links']]}
        group['fingerprint'] = hashlib.sha256(json.dumps(physical,sort_keys=True,
            allow_nan=False,separators=(',',':')).encode()).hexdigest()
    return result


def _scope(group, serial):
    """Give changed actors new paths, while preserving public source IDs."""
    key = group['item']['id'] if group['kind']=='body' else group['spring']['id']
    entity = '/World/SceneInputs/Entities/'+author._identifier('Entity'+group['kind'],key)+'_r'+str(serial)
    group['entity_path'] = entity
    if group['kind']=='body':
        item = group['item']
        item['path'],item['material_path'] = entity+'/Body',entity+'/Material'
    else:
        spring = group['spring']
        spring['anchor']['path'] = entity+'/Anchor'
        for link in spring['links']:
            link['path'] = entity+'/'+author._identifier('Link',link['id'])
            link['material_path'] = entity+'/'+author._identifier('Material',link['id'])
        for link in spring['links']:
            link['filtered_paths'] = [v['path'] for v in spring['links'] if v is not link]


def _source(group, preserved):
    group = copy.deepcopy(group)
    entity = group['entity_path']
    # USD references remap relationships inside their source subtree. Author
    # targets at /Entity/...; native /World/... targets lie outside that source
    # subtree and USD would discard them rather than remap them.
    for item in group['items']:
        item['path'] = '/Entity'+item['path'][len(entity):]
        if item.get('material_path','').startswith(entity+'/'):
            item['material_path'] = '/Entity'+item['material_path'][len(entity):]
        if item.get('filtered_paths'):
            item['filtered_paths'] = ['/Entity'+path[len(entity):] for path in item['filtered_paths']]
    children = []
    if group['kind']=='body':
        item = group['item']
        body = copy.deepcopy(item)
        if item['id'] in preserved:
            body['pose'] = preserved[item['id']]['pose']
        children = [author._material(item),author._body(body)]
    else:
        spring = group['spring']
        anchor = copy.deepcopy(spring['anchor'])
        anchor_id = ('anchor',spring['id'])
        if anchor_id in preserved:
            anchor['pose'] = preserved[anchor_id]['pose']
        children.append(author._body(anchor,anchor=True))
        parent = spring['anchor']
        by_id = {item['id']:item for item in spring['links']}
        for index,link in enumerate(spring['links']):
            if 'parent_id' in link:
                parent = spring['anchor'] if link['parent_id'] is None else by_id[str(link['parent_id'])]
            body = copy.deepcopy(link)
            if link['id'] in preserved:
                body['pose'] = preserved[link['id']]['pose']
            children += [author._material(link),author._body(body),author._joint(spring,link,parent,index)]
            parent = link
    return '#usda 1.0\n(defaultPrim = "Entity")\n'+author._prim('Xform','Entity',[],children=children)


class IncrementalScene:
    def __init__(self,physx,stage):
        self.physx,self.stage = physx,stage
        self.entities = {}
        self.serial = 0
        self.helper = author.NativeScene(physx,stage,normalize({}))
        self.last_changes = {'added':0,'removed':0,'updated':0,'unchanged':0}

    @property
    def spec(self):
        return self.helper.spec

    def _capture(self,group):
        result = {}
        for item in group['items']:
            key = ('anchor',group['spring']['id']) if group['kind']=='spring' and item is group['spring']['anchor'] else item['id']
            if item['mode']=='STATIC':
                result[key] = {'pose':item['pose'][:],'velocity':[0.]*6}
            else:
                result[key] = {'pose':self.helper._read(item,'pose'),
                               'velocity':self.helper._read(item,'velocity')}
        return result

    def reconcile(self,config,spec=None):
        import ovstage
        spec = normalize(config) if spec is None else spec
        fresh = _groups(spec)
        old = self.entities
        added = set(fresh)-set(old)
        removed = set(old)-set(fresh)
        changed = {key for key in set(old)&set(fresh) if old[key]['fingerprint']!=fresh[key]['fingerprint']}
        stable = (set(old)&set(fresh))-changed
        preserved = {}
        for key in changed:
            preserved.update(self._capture(old[key]))
        targets = {item['id']:self.helper.targets[item['path']][:]
                   for group in old.values() for item in group['items']
                   if item['path'] in self.helper.targets}
        for key in stable:
            prior,current = old[key],fresh[key]
            if prior['kind']=='body':
                prior['item']['name'] = current['item']['name']
            else:
                prior['spring']['name'] = current['spring']['name']
                for before,after in zip(prior['spring']['links'],current['spring']['links']):
                    before['bone'],before['name'] = after['bone'],after['name']
            fresh[key] = prior
        # Point the committed spec at retained native records for unchanged IDs.
        for field in ('objects','colliders'):
            spec[field] = [fresh[('body',item['id'])]['item'] for item in spec[field]]
        spec['springs'] = [fresh[('spring',spring['id'])]['spring'] for spring in spec['springs']]
        changes = {'added':len(added),'removed':len(removed),'updated':len(changed),'unchanged':len(stable)}
        if not (added or removed or changed):
            self.helper.spec = spec
            self.entities = fresh
            self.last_changes = changes
            return changes
        sources = {}
        for key in added|changed:
            self.serial += 1
            _scope(fresh[key],self.serial)
            sources[key] = _source(fresh[key],preserved)
        # Parsing all new USD sources happens before deleting native entities.
        # A parse failure is rejectable: no PhysX delta has been consumed yet.
        new_handles = {}
        try:
            for key in added|changed:
                new_handles[key] = ovstage.population.add_usd_reference_from_string(
                    self.stage,sources[key],fresh[key]['entity_path'])
        except Exception:
            try:
                for handle in new_handles.values():
                    ovstage.population.remove_usd(self.stage,handle)
            except Exception as error:
                raise FatalSceneError('Failed to discard uncommitted USD scene edits: '+str(error)) from error
            raise
        ordinal = self.helper.ordinal+1
        self.helper.close()
        try:
            for key in removed|changed:
                ovstage.population.remove_usd(self.stage,old[key]['handle'])
            ovstage.population.apply_usd_changes(self.stage,ordinal=ordinal)
            self.stage.advance_write_floor(ordinal=ordinal).wait()
            self.physx.update_from_ovstage(ordinal,ordinal)
            helper = author.NativeScene(self.physx,self.stage,spec)
            helper.ordinal = ordinal
            for group in fresh.values():
                for item in group['items']:
                    key = ('anchor',group['spring']['id']) if group['kind']=='spring' and item is group['spring']['anchor'] else item['id']
                    saved = preserved.get(key)
                    if saved and item['mode']!='STATIC':
                        helper._write(item,'pose',saved['pose'])
                        if item['mode']=='DYNAMIC':
                            helper._write(item,'velocity',saved['velocity'])
                        else:
                            helper.targets[item['path']] = saved['pose'][:]
                    elif item['path'] in helper.targets and item['id'] in targets:
                        helper.targets[item['path']] = targets[item['id']][:]
            for key,handle in new_handles.items():
                fresh[key]['handle'],fresh[key]['source'] = handle,sources[key]
            self.helper,self.entities,self.last_changes = helper,fresh,changes
            return changes
        except Exception as error:
            raise FatalSceneError('Native PhysX scene edit failed after commit: '+str(error)) from error

    def reset(self):
        self.helper.reset()

    def update(self,value):
        self.helper.update(value)

    def before_step(self,dt,smplstate=None):
        self.helper.before_step(dt,smplstate)

    def snapshot(self):
        result = self.helper.snapshot()
        result['scene_changes'] = dict(self.last_changes)
        result['scene_ordinal'] = self.helper.ordinal
        return result

    def close(self):
        self.helper.close()
