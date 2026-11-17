import unittest
import tempfile
from pathlib import Path
from item_exclusions import ExclusionPolicy, excluded_family, excluded_icons, identity

def item(key,tag='{}',short=None):
    return dict(key=key,nbt=tag,shortKey=short or key)

class ExclusionsTest(unittest.TestCase):
    def test_exact_family_boundaries(self):
        for key in ('ExtraUtilities:microblocks','ExtraUtilities:microblocks:0','BuildCraft|Transport:pipeFacade','appliedenergistics2:item.ItemFacade:0','ForgeMicroblock:microblock:513'):
            self.assertTrue(excluded_family(key),key)
        for key in ('ExtraUtilities:microblocks:1','ExtraUtilities:microblocks:2','ForgeMicroblock:sawStone','ForgeMicroblock:stoneRod','LittleTiles:LTBlockTile'):
            self.assertFalse(excluded_family(key),key)

    def test_retained_recipe_dependencies_reach_fixed_point(self):
        a=item('ForgeMicroblock:microblock:513','{mat:"a"}')
        b=item('ForgeMicroblock:microblock:514','{mat:"a"}')
        disposable=item('ForgeMicroblock:microblock:513','{mat:"b"}')
        recipes=[dict(inputItems=[a],outputItems=[item('minecraft:diamond')]),
                 dict(inputItems=[b],outputItems=[a]),dict(inputItems=[],outputItems=[disposable])]
        policy=ExclusionPolicy([a,b,disposable],[dict(recipes=list(reversed(recipes)))])
        self.assertEqual(policy.protected,{identity(a),identity(b)})
        self.assertTrue(policy.keep_recipe(recipes[1]))
        self.assertFalse(policy.keep_recipe(recipes[2]))

    def test_quest_and_shared_icons_are_preserved(self):
        needed=item('ForgeMicroblock:microblock:513','{mat:"a"}','needed')
        removed=item('ForgeMicroblock:microblock:513','{mat:"b"}','removed')
        other=item('minecraft:stone',short='stone')
        quest=dict(questId=1,inputItems=[needed],outputItems=[item('bq_standard:quest')])
        policy=ExclusionPolicy([needed,removed,other],[dict(recipes=[quest])])
        self.assertTrue(policy.keep_item(needed));self.assertFalse(policy.keep_item(removed))
        icons=[dict(shortKey='needed',iconName='needed.png'),dict(shortKey='removed',iconName='shared.png',frameCount=2),dict(shortKey='stone',iconName='shared.png',frameCount=2)]
        self.assertEqual(excluded_icons([needed,removed,other],icons,policy),set())

    def test_cached_alias_to_excluded_icon_reverts_to_retained_file(self):
        from image_plan import build_plan
        removed=item('ForgeMicroblock:microblock:513','{mat:"b"}','removed')
        kept=item('minecraft:stone',short='kept')
        icons=[dict(shortKey='removed',iconName='removed.png'),dict(shortKey='kept',iconName='kept.png')]
        policy=ExclusionPolicy([removed,kept],[])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in ('removed.png','kept.png'):(root/name).write_bytes(b'cached')
            plan=build_plan([removed,kept],icons,root,{'File:旧装饰名.png':'removed.png','File:石头.png':'kept.png'},cached_plan=dict(schemaVersion=1,keyAliases={'kept.png':'removed.png'}),policy=policy)
            self.assertEqual(plan['uploadIcons'],['kept.png'])
            self.assertEqual(plan['redirects'],{'File:石头.png':'kept.png'})

if __name__=='__main__':unittest.main()
