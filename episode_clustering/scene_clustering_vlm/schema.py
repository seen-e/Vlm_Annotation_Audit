STATUSES={'coherent','singleton','uncertain'}


def string(value):
    if not isinstance(value,str) or not value.strip():raise ValueError('Expected nonempty text')


def unique_list(value):
    if not isinstance(value,list) or not value:raise ValueError('Expected nonempty list')
    for v in value:string(v)
    if len(value)!=len(set(value)):raise ValueError('Duplicate IDs')


def base(result,fields,id_key):
    if not isinstance(result,dict) or set(result)!={'groups'} or not isinstance(result['groups'],list) or not result['groups']:
        raise ValueError('Expected groups array')
    ids=[]
    for g in result['groups']:
        if not isinstance(g,dict) or set(g)!=fields:raise ValueError('Invalid group fields')
        string(g[id_key]);ids.append(g[id_key])
        if g['status'] not in STATUSES:raise ValueError('Invalid status')
        string(g['common_features'])
    if len(ids)!=len(set(ids)):raise ValueError('Duplicate group IDs')


def validate_round1(result,expected,representatives):
    base(result,{'local_group_id','status','member_episode_ids','representative_episode_ids','common_features','distinguishing_features'},'local_group_id')
    members=[]
    for g in result['groups']:
        unique_list(g['member_episode_ids']);unique_list(g['representative_episode_ids']);string(g['distinguishing_features'])
        m=g['member_episode_ids'];r=g['representative_episode_ids']
        if not set(r)<=set(m) or len(r)!=min(representatives,len(m)):raise ValueError('Invalid representatives')
        if (g['status']=='coherent' and len(m)<2) or (g['status']!='coherent' and len(m)!=1):raise ValueError('Status/member count mismatch')
        members.extend(m)
    if len(members)!=len(set(members)) or set(members)!=set(expected):raise ValueError('Episode coverage mismatch')


def validate_round2(result,candidates):
    base(result,{'final_group_id','source_group_ids','status','common_features','reason'},'final_group_id')
    covered=[]
    for g in result['groups']:
        unique_list(g['source_group_ids']);string(g['reason']);refs=g['source_group_ids']
        if not set(refs)<=set(candidates):raise ValueError('Unknown candidate group')
        n=sum(len(candidates[k]['member_episode_ids']) for k in refs)
        if g['status']=='singleton' and n!=1 or g['status']=='coherent' and n<2:raise ValueError('Status/count mismatch')
        if g['status']=='uncertain' and len(refs)!=1:raise ValueError('Do not merge uncertain groups')
        covered.extend(refs)
    if len(covered)!=len(set(covered)) or set(covered)!=set(candidates):raise ValueError('Candidate coverage mismatch')
