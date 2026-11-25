import unittest
import tempfile
from pathlib import Path
from item_exclusions import (ExclusionPolicy, convert_fluid_displays, excluded_icons, fluid_names, identity,
                             is_restyle, item_family, quest_item_keys)

def item(key,tag='{}',short=None):
    return dict(key=key,nbt=tag,shortKey=short or key)

class ExclusionsTest(unittest.TestCase):
    def test_exact_family_boundaries(self):
        for key in ('ExtraUtilities:microblocks','ExtraUtilities:microblocks:1','ExtraUtilities:microblocks:3','BuildCraft|Transport:pipeFacade','appliedenergistics2:item.ItemFacade:0','ForgeMicroblock:microblock:513','NotEnoughItems:neiFluidDisplay:5'):
            self.assertTrue(item_family(item(key)),key)
        for key in ('ForgeMicroblock:sawStone','ForgeMicroblock:stoneRod','LittleTiles:LTBlockTile','IC2:itemFluidCell','gregtech:gt.GregTech_FluidDisplay:5','TConstruct:pickaxe'):
            self.assertIsNone(item_family(item(key,'{Fluid:{FluidName:"water"}}')),key)
        banner='{BlockEntityTag:{Patterns:[0:{Pattern:"bri",Color:0}]}}'
        self.assertEqual(item_family(item('etfuturum:banner',banner)),'banner')
        self.assertIsNone(item_family(item('etfuturum:banner:15')))
        self.assertEqual(item_family(item('gregtech:gt.Volumetric_Flask','{Fluid:{Amount:1000,FluidName:"water"}}')),'container')
        self.assertIsNone(item_family(item('gregtech:gt.Volumetric_Flask','{Capacity:144}')))
        self.assertEqual(item_family(item('WitchingGadgets:item.WG_CrystalFlask','{fluid:"water"}')),'container')
        self.assertIsNone(item_family(item('WitchingGadgets:item.WG_CrystalFlask')))

    def test_retained_recipe_dependencies_reach_fixed_point(self):
        a=item('ForgeMicroblock:microblock:513','{mat:"a"}')
        b=item('ForgeMicroblock:microblock:514','{mat:"a"}')
        disposable=item('ForgeMicroblock:microblock:513','{mat:"b"}')
        recipes=[dict(inputItems=[a],outputItems=[item('minecraft:diamond')]),
                 dict(inputItems=[b],outputItems=[a]),dict(inputItems=[],outputItems=[disposable])]
        policy=ExclusionPolicy([a,b,disposable],[dict(recipes=list(reversed(recipes)))])
        self.assertEqual(policy.protected,{identity(a),identity(b)})
        kept,counts=policy.filter_recipes(recipes)
        self.assertEqual(kept,recipes[:2])
        self.assertEqual(counts,{'decorative_variants':1})

    def test_bolts_keep_one_recipe_per_head_and_page(self):
        def bolt(head,shaft,short):
            return item(f'TConstruct:BoltPart:{head}',f'{{DualMat:{{Material2:{shaft}}}}}',short)
        items=[bolt(9,5,'creative'),bolt(9,10,'low'),bolt(9,20,'high'),bolt(9,30,'quest'),bolt(3,40,'only'),bolt(7,8,'spare'),bolt(7,2,'fallback')]
        def made(output):return dict(inputItems=['TConstruct:toolRod'],outputItems=[output])
        page=[made(items[2]),made(items[1]),made(items[4])]
        policy=ExclusionPolicy(items,[dict(recipes=page)],quest_keys={'quest'})
        self.assertEqual([i['shortKey'] for i in items if policy.keep_item(i)],['low','quest','only','fallback'])
        kept,counts=policy.filter_recipes(page)
        self.assertEqual(kept,page[1:])
        self.assertEqual(counts,{'bolt_variants':1})

    def test_ztones_restyle_only(self):
        restyle=dict(inputItems=['Ztones:ofanix','Ztones:tile.syncBlock:4',None,None],outputItems=['Ztones:tile.syncBlock'])
        other_block=dict(inputItems=['Ztones:ofanix','Ztones:tile.zetaBlock:4'],outputItems=['Ztones:tile.syncBlock'])
        crafted=dict(inputItems=['Ztones:stoneTile','#ingotSteel'],outputItems=['Ztones:tile.syncBlock'])
        self.assertTrue(is_restyle(restyle))
        self.assertFalse(is_restyle(other_block));self.assertFalse(is_restyle(crafted))
        kept,counts=ExclusionPolicy([],[]).filter_recipes([restyle,crafted])
        self.assertEqual((kept,dict(counts)),([crafted],{'ztones_restyle':1}))

    def test_containers_never_remove_recipes(self):
        used=item('gregtech:gt.Volumetric_Flask','{Fluid:{Amount:1000,FluidName:"water"}}','used')
        unused=item('gregtech:gt.Volumetric_Flask','{Fluid:{Amount:1000,FluidName:"lava"}}','unused')
        empty=item('gregtech:gt.Volumetric_Flask',short='empty')
        filling=dict(inputItems=['gregtech:gt.Volumetric_Flask'],outputItems=[used])
        policy=ExclusionPolicy([used,unused,empty],[dict(recipes=[filling])])
        self.assertEqual([i['shortKey'] for i in (used,unused,empty) if policy.keep_item(i)],['used','empty'])
        self.assertEqual(policy.filter_recipes([filling])[0],[filling])

    def test_patterned_banner_recipes_leave_with_the_banners(self):
        patterned=item('etfuturum:banner','{BlockEntityTag:{Patterns:[0:{Pattern:"bri"}]}}')
        recipe=dict(inputItems=['etfuturum:banner','#dyeBlack'],outputItems=[patterned])
        policy=ExclusionPolicy([patterned,item('etfuturum:banner')],[dict(recipes=[recipe])])
        self.assertFalse(policy.keep_item(patterned));self.assertTrue(policy.keep_item(item('etfuturum:banner')))
        self.assertEqual(policy.filter_recipes([recipe]),([],{'patterned_banners':1}))

    def test_nei_fluid_displays_become_fluid_slots(self):
        names=fluid_names([dict(key='fluid.molten.gold',gtDisplayKey='gregtech:gt.GregTech_FluidDisplay:462'),
                           dict(key='fluid.water',gtDisplayKey='gregtech:gt.GregTech_FluidDisplay:1')])
        gold=dict(key='NotEnoughItems:neiFluidDisplay:462',nbt='{neiFluidDisplayAmount:144L}')
        casting=dict(inputItems=[gold,'minecraft:emerald'],outputItems=['TConstruct:metalPattern:26'])
        alloy=dict(inputItems=[dict(gold),['NotEnoughItems:neiFluidDisplay:1','NotEnoughItems:neiFluidDisplay:462']],
                   outputItems=[dict(gold)],inputFluids=[dict(key='fluid.lava',amount=5)])
        mixed=dict(inputItems=[['NotEnoughItems:neiFluidDisplay:1','minecraft:stone']],outputItems=['NotEnoughItems:neiFluidDisplay:7'])
        unresolved=set()
        self.assertEqual(convert_fluid_displays([casting,alloy,mixed],names,unresolved),6)
        shown=dict(key='fluid.molten.gold',amount=144,showAmount=True)
        self.assertEqual(casting['inputItems'],['minecraft:emerald'])
        self.assertEqual(casting['inputFluids'],[shown])
        self.assertEqual(alloy['inputItems'],[])
        self.assertEqual(alloy['inputFluids'],[dict(key='fluid.lava',amount=5),shown,[dict(key='fluid.water'),dict(key='fluid.molten.gold')]])
        self.assertEqual((alloy['outputItems'],alloy['outputFluids']),([],[shown]))
        self.assertEqual(mixed['inputItems'],[['fluid.water','minecraft:stone']])
        self.assertEqual(mixed['outputItems'],['NotEnoughItems:neiFluidDisplay:7'])
        self.assertNotIn('outputFluids',mixed)
        self.assertEqual(unresolved,{'NotEnoughItems:neiFluidDisplay:7'})
        self.assertFalse(ExclusionPolicy([],[]).keep_item(item('NotEnoughItems:neiFluidDisplay:462')))

    def test_quest_keys_ignore_task_matches(self):
        import json
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp,'betterquesting');folder.mkdir()
            Path(folder,'itemKeys.json').write_text(json.dumps({'q':{'icon':'a','tasks':{'0':['b',None]},'rewards':{'0':['c']},'taskMatches':{'0':[['d']]}}}),'utf-8')
            self.assertEqual(quest_item_keys(temp),{'a','b','c'})

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
