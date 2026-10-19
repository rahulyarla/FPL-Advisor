"""Exact pairwise metrics, missing coverage, local storage and HTTP-job contracts."""

import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fpl_advisor.http import DataError, write_json
from fpl_advisor.integrity import (ids, settings, snapshot, weekly, moves, summarize, analyse,
                                  options, SimilarityJob, PublicReader, Cancelled)


def picks(players=range(1,16), gw=3, captain=None, chip=None):
    players=list(players);captain=captain or players[0]
    return {"entry_history":{"event":gw},"active_chip":chip,"picks":[{"element":p,"position":i+1,
             "is_captain":p==captain,"is_vice_captain":p==players[1],"multiplier":0 if i==0 else 1} for i,p in enumerate(players)]}


def entry(number=1, weeks=(1,2,3), players=range(1,16)):
    return {"id":number,"name":"Team "+str(number),"weeks":{str(w):snapshot(picks(players,gw=w),w) for w in weeks},"warnings":[]}


def config(**values):
    return settings({"group_a":[1],"group_b":[2],"start_gw":1,"end_gw":3,**values},7)


def metadata():
    now=datetime.now(timezone.utc)
    return {"events":[{"id":w,"deadline_time":(now+timedelta(days=w-4)).isoformat(),"finished":w<3} for w in range(1,6)],
            "elements":[{"id":i,"web_name":"Player "+str(i),"element_type":1} for i in range(1,40)],"teams":[{"id":1,"name":"Example"}]}


class FakeReader:
    def __init__(self,root=None,cancelled=None,force=False):self.cancelled=cancelled or threading.Event();self.season=""
    def json(self,path,ttl=900):
        if self.cancelled.is_set():raise Cancelled("Cancelled")
        if path=='bootstrap-static/':return metadata()
        parts=path.strip('/').split('/')
        if len(parts)==2:return {"name":"Team "+parts[1],"started_event":1}
        number,gw=int(parts[1]),int(parts[3]);players=range(1,16) if number in (1,2) else range(4,19)
        return picks(players,gw)


class IntegrityContracts(unittest.TestCase):
    def test_ids_accept_bulk_lists_links_and_deduplicate(self):
        self.assertEqual(ids('1, 2\nhttps://fantasy.premierleague.com/entry/3/event/2/;1'),[1,2,3])
        for bad in ['',[],['-1'],['0'],['1.2'],['https://elsewhere.test/entry/1/'],[True],[10**14]]:
            with self.subTest(value=bad),self.assertRaises(DataError):ids(bad)

    def test_all_threshold_endpoints_and_invalid_values(self):
        for value in (0,100,86.7):self.assertEqual(config(threshold=value)['threshold'],value)
        for value in (-1,101,float('nan'),float('inf'),True,'x'):
            with self.subTest(value=value),self.assertRaises(DataError):config(threshold=value)
        for change in ({'start_gw':4,'end_gw':3},{'end_gw':8},{'start_gw':1.5},{'metric':'guilt'},{'min_matching_week_percent':101}):
            with self.assertRaises(DataError):config(**change)

    def test_snapshot_uses_positions_and_not_scored_multipliers(self):
        data=picks();data['picks'][0]['multiplier']=0;data['picks'][12]['multiplier']=2
        s=snapshot(data,3);self.assertIn(1,s['xi']);self.assertNotIn(13,s['xi']);self.assertEqual(len(s['xi']),11)

    def test_malformed_or_wrong_gameweek_snapshot_is_rejected(self):
        bad=picks();bad['picks'][0]['element']=bad['picks'][1]['element']
        for value in (bad,picks(gw=4),{'picks':[]},[],{'picks':[None]}):
            with self.assertRaises(DataError):snapshot(value,3)

    def test_identical_squads_xis_and_captains_are_100(self):
        row=weekly(entry(1),entry(2),3)
        for key in ('squad','xi','combined','captain','vice_captain','bench_order'):self.assertEqual(row[key],100)

    def test_twelve_shared_players_are_80_percent_not_jaccard(self):
        row=weekly(entry(1),entry(2,players=range(4,19)),3)
        self.assertEqual(len(row['shared_squad']),12);self.assertEqual(row['squad'],80)
        self.assertAlmostEqual(row['xi'],100*8/11)
        self.assertEqual(row['left_only'],[1,2,3]);self.assertEqual(row['right_only'],[16,17,18])

    def test_combined_score_and_captain_agreement_are_separate(self):
        b=entry(2);b['weeks']['3']['captain']=2
        row=weekly(entry(),b,3);self.assertEqual(row['captain'],0);self.assertEqual(row['combined'],90)

    def test_unknown_captains_do_not_become_matching_or_zero(self):
        a,b=entry(),entry(2);a['weeks']['3']['captain']=None;b['weeks']['3']['captain']=None
        row=weekly(a,b,3);self.assertIsNone(row['captain']);self.assertIsNone(row['combined'])
        self.assertFalse(summarize(a,b,config(metric='combined'))['complete'])

    def test_thresholds_are_inclusive_and_use_unrounded_values(self):
        a,b=entry(),entry(2,players=range(4,19))
        self.assertTrue(summarize(a,b,config(threshold=80))['meets_threshold'])
        self.assertFalse(summarize(a,b,config(threshold=80.1))['meets_threshold'])
        b=entry(2,players=range(3,18))
        self.assertFalse(summarize(a,b,config(threshold=86.7))['meets_threshold'])
        self.assertTrue(summarize(a,b,config(threshold=0))['meets_threshold'])
        self.assertTrue(summarize(a,entry(2),config(threshold=100))['meets_threshold'])

    def test_period_changes_use_only_selected_weeks(self):
        a,b=entry(),entry(2);b['weeks']['3']=snapshot(picks(range(16,31),3),3)
        self.assertEqual(summarize(a,b,config(end_gw=2))['average'],100)
        self.assertAlmostEqual(summarize(a,b,config())['average'],200/3)

    def test_missing_week_is_not_zero_filled_or_a_positive_finding(self):
        a,b=entry(),entry(2,weeks=(1,3))
        r=summarize(a,b,config(threshold=80),details=True)
        self.assertEqual(r['average'],100);self.assertEqual(r['compared_weeks'],2)
        self.assertIsNone(r['meets_threshold']);self.assertEqual(r['status'],'Insufficient coverage');self.assertEqual(r['longest_streak'],1)
        self.assertFalse(r['weeks'][1]['available'])

    def test_repeated_week_filter_is_additional_to_average(self):
        a,b=entry(),entry(2);b['weeks']['3']=snapshot(picks(range(4,19),3),3)
        # Average >90, but only two weeks reach a 90% weekly threshold.
        r=summarize(a,b,config(threshold=90,min_matching_week_percent=100))
        self.assertFalse(r['meets_threshold']);self.assertEqual(r['weeks_above'],2);self.assertEqual(r['longest_streak'],2)

    def test_all_a_by_b_pairs_are_independent_and_self_pairs_skipped(self):
        cfg=config(group_a=[1,2,1],group_b=[2,3,3])
        dataset={'season':'2026-27','checked_at':'2026-10-19T00:00:00Z','entries':{str(n):entry(n) for n in (1,2,3)},'players':{},'events':[]}
        report=analyse(dataset,cfg)
        self.assertEqual({(p['a'],p['b']) for p in report['pairs']},{(1,2),(1,3),(2,3)})
        self.assertEqual(report['self_pairs_skipped'],1)

    def test_no_actions_and_free_hit_transitions_do_not_match_transfers(self):
        a,b=entry(),entry(2);self.assertIsNone(weekly(a,b,3)['changes'])
        a['weeks']['2']['chip']='freehit';self.assertIsNone(moves(a,3));self.assertIsNone(moves(a,2))

    def test_real_inferred_actions_and_wildcards_remain_visible(self):
        a,b=entry(),entry(2)
        for e in (a,b):e['weeks']['3']=snapshot(picks([*range(1,15),16],3,chip='wildcard'),3)
        r=weekly(a,b,3);self.assertEqual(r['changes'],100);self.assertEqual(r['left_moves'],{'in':[16],'out':[15]})

    def test_metadata_excludes_future_deadlines(self):
        with tempfile.TemporaryDirectory() as directory:
            o=options(directory,FakeReader());self.assertEqual(o['latest_gw'],4)
            self.assertEqual([e['id'] for e in o['events']],[1,2,3,4])

    def test_job_persists_privately_and_recalculates_without_new_reads(self):
        with tempfile.TemporaryDirectory() as directory,patch('fpl_advisor.integrity.PublicReader',FakeReader):
            job=SimilarityJob(directory);job.start(config(group_a=[1,3],group_b=[2],end_gw=3))
            until=time.monotonic()+5
            while job.status()['running'] and time.monotonic()<until:time.sleep(.01)
            self.assertIsNone(job.status()['error']);self.assertEqual(len(job.report()['pairs']),2)
            with patch('fpl_advisor.integrity.PublicReader',side_effect=AssertionError('Unexpected read')):
                r=job.recalculate({'threshold':100,'start_gw':2});self.assertEqual(r['settings']['start_gw'],2)
            self.assertEqual(len(job.pair({'a':1,'b':2})['weeks']),2)
            with self.assertRaises(DataError):job.pair({'a':2,'b':1})
            with self.assertRaises(DataError):job.recalculate({'end_gw':4})
            self.assertIn('average',job.csv());self.assertNotIn('Team 1',job.csv())
            self.assertTrue((Path(directory)/'user/integrity/latest.json').exists());self.assertFalse((Path(directory)/'reports').exists())

    def test_cancelled_run_does_not_replace_previous_report(self):
        class SlowReader(FakeReader):
            def json(self,path,ttl=900):
                time.sleep(.03)
                return super().json(path,ttl)
        with tempfile.TemporaryDirectory() as directory,patch('fpl_advisor.integrity.PublicReader',SlowReader):
            path=Path(directory)/'user/integrity/latest.json';write_json(path,{'ready':True,'marker':'old'})
            job=SimilarityJob(directory);job.start(config());job.cancel()
            until=time.monotonic()+5
            while job.status()['running'] and time.monotonic()<until:time.sleep(.01)
            self.assertIsNotNone(job.status()['error']);self.assertEqual(job.report()['marker'],'old')

    def test_invalid_request_does_not_launch_network_job(self):
        with tempfile.TemporaryDirectory() as directory:
            job=SimilarityJob(directory)
            for p in ({'group_a':[],'group_b':[1]},{**config(),'refresh':'yes'}):
                with self.assertRaises(DataError):job.start(p)

    def test_cache_scope_separates_seasons_and_cancel_stops_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            reader=PublicReader(directory);reader.cancelled.set()
            with self.assertRaises(Cancelled):reader.json('entry/1/')


if __name__=='__main__':unittest.main()
