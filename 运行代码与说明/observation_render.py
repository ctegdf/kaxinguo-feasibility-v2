"""One observed value rendered as text; no additional inference."""

def observation_summary(data, shared_context):
    """Render the same one observed fact, without interpreting another fact."""
    field, value, view = data['field'], data['value'], data['view_id']
    names = {loc['location_id']:loc['name'] for loc in shared_context['Semantic_Map']['locations']}
    place = names[data['location_id']]
    if field == 'people_count':
        return f'{place}的当前指定视野里看到了{value}个人。'
    if field == 'people_descriptions':
        return '当前指定视野中的人物外观：'+('、'.join(value) if value else '没有看到人')+'。'
    if field == 'cup_visible':
        area = '书桌上' if view == 'view_study_top_right_overview' else '厨房操作台上'
        return area+('看到了杯子。' if value else '没有看到杯子。')
    if field == 'tableware_visible':
        return '餐桌上'+('看到了餐具。' if value else '没有看到餐具。')
    if field == 'quiet_sign_visible':
        return '书桌上'+('有' if value else '没有')+'“请勿打扰”标识。'
    if field == 'notice_text':
        return '通知卡写着“'+value+'”。' if value else '没有看到通知卡。'
    if field == 'visible_items':
        area = '书桌上' if view == 'view_study_top_right_overview' else '厨房操作台上'
        return area+('看到了'+'、'.join(value)+'。' if value else '没有看到物品。')
    raise ValueError('UNKNOWN_OBSERVATION_FIELD')

