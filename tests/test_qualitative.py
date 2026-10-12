"""Source failure, attribution, bounds and real deadline-safe learning contracts."""

import gzip
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from fpl_advisor.http import DataError, write_json
from fpl_advisor.pipeline import selection, state
from fpl_advisor.qual_sources import atom, import_text, save_settings, youtube, settings, linked_official
from fpl_advisor.qualitative import evidence, apply_evidence, archive_pair, evaluate, VERSION
from fpl_advisor.data import season_order
from test_contracts import player_pool, record


class QualitativeContracts(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.manifest = {"season": "2026-27", "next_gameweek": 6, "forecast_weeks": [6, 7], "completed_gameweeks": [1, 2, 3, 4, 5],
                         "deadline": (self.now + timedelta(days=2)).isoformat(), "created_at": (self.now - timedelta(hours=1)).isoformat(),
                         "forecast_created_at": (self.now - timedelta(hours=1)).isoformat(), "live_refresh": True,
                         "model_version": "feedback-ensemble-v3.0", "model_trained_through": season_order("2026-27", 5)}
        self.players = [{"id": 1, "code": 101, "name": "Haaland", "full_name": "Erling Haaland", "position": "FWD", "availability": 1,
                         "minutes_recent": 90, "gw6": 6, "gw7": 7, "games6": 1, "next_points": 6, "horizon_points": 13, "weighted_points": 11.6},
                        {"id": 2, "code": 102, "name": "Saka", "full_name": "Bukayo Saka", "position": "MID", "availability": 1,
                         "minutes_recent": 90, "gw6": 5, "gw7": 5, "games6": 1, "next_points": 5, "horizon_points": 10, "weighted_points": 9}]

    def doc(self, text, source="Creator A", kind="youtube", **values):
        return {"id": source, "text": text, "source": source, "url": "https://www.youtube.com/watch?v=abcdefghijk", "kind": kind,
                "published_at": (self.now - timedelta(hours=2)).isoformat(), "fetched_at": self.now.isoformat(), "title": "FPL GW6", "coverage": "transcript", **values}

    def extract(self, docs):
        return evidence({"documents": docs, "settings": {"lookback_days": 7}}, self.players, self.manifest, self.now.isoformat())

    def test_transcript_attribution_and_link_timestamp(self):
        d = self.doc("")
        d["segments"] = [{"text": "Haaland is a great pick.", "start": 95, "duration": 4}]
        result = self.extract([d])
        self.assertEqual(result["signals"][0]["player_id"], 1)
        self.assertEqual(result["signals"][0]["timestamp"], 95)

    def test_sentence_boundaries_keep_other_players_and_negation_separate(self):
        result = self.extract([self.doc("Haaland is a great pick. Saka is not a good option.")])
        self.assertEqual([(s["player_id"], s["kind"]) for s in result["signals"]], [(1, "sentiment_positive")])
        self.assertTrue(result["review"])

    def test_uncertain_sarcastic_and_multiple_player_passages_have_no_effect(self):
        for text in ["If Haaland starts he is essential", "Haaland is essential lol /s", "Haaland and Saka are great picks", "Haaland might be injured", "Haaland is not ruled out"]:
            with self.subTest(text=text):
                result = self.extract([self.doc(text)])
                self.assertFalse(result["signals"])
                self.assertTrue(result["review"])

    def test_duplicate_reposts_are_one_vote(self):
        result = self.extract([self.doc("Haaland is a great pick."), self.doc("Haaland is a great pick.", source="Creator B")])
        self.assertEqual(len(result["signals"]), 1)
        self.assertEqual(result["duplicates_removed"], 1)

    def test_two_videos_by_same_creator_do_not_corroborate_news(self):
        result = self.extract([self.doc("Haaland will start."), self.doc("Haaland is a nailed starter.", url="https://www.youtube.com/watch?v=bbbbbbbbbbb")])
        apply_evidence(self.players, [6, 7], result)
        self.assertEqual(result["players"]["1"]["news_fraction"], 0)

    def test_shared_original_report_does_not_become_two_sources(self):
        original = "https://www.mancity.com/news/report"
        result = self.extract([self.doc("Haaland will start.", origin_url=original), self.doc("Haaland is a nailed starter.", source="Creator B", origin_url=original)])
        apply_evidence(self.players, [6, 7], result)
        self.assertEqual(result["players"]["1"]["news_fraction"], 0)

    def test_future_stale_and_wrong_gameweek_evidence_is_not_used(self):
        for d in [self.doc("Haaland is essential.", published_at=(self.now+timedelta(days=1)).isoformat()),
                  self.doc("Haaland is essential.", title="FPL GW5"), self.doc("Haaland is essential.", title="FPL GW6 2025/26")]:
            self.assertFalse(self.extract([d])["signals"])
        stale=self.doc("Haaland is essential.", published_at=(self.now-timedelta(days=3)).isoformat(), fetched_at=(self.now-timedelta(days=2)).isoformat())
        result=self.extract([stale]);candidate,_=apply_evidence(self.players,[6,7],result)
        self.assertEqual(candidate[0]["gw6"],6)
        self.assertTrue(result["signals"][0]["stale"])

    def test_metadata_only_titles_never_become_transcript_evidence(self):
        result=self.extract([self.doc("Haaland is essential.", coverage="description_only")])
        candidate,_=apply_evidence(self.players,[6,7],result)
        self.assertEqual(candidate[0]["gw6"],6)
        self.assertFalse(result["signals"][0]["usable"])

    def test_opinion_bounds_and_future_weeks_unchanged(self):
        result=self.extract([self.doc("Haaland is essential.")]);candidate,learned=apply_evidence(self.players,[6,7],result)
        self.assertLessEqual(abs(candidate[0]["gw6"]-6),.25)
        self.assertEqual(candidate[0]["gw7"],7)
        self.assertEqual(learned,self.players)
        self.assertEqual(self.players[0]["next_points"],6)

    def test_official_availability_is_not_counted_twice(self):
        players=[{**self.players[0],"availability":.5}]
        result=self.extract([self.doc("Haaland is ruled out.",kind="official",url="https://www.mancity.com/news/report")])
        candidate,_=apply_evidence(players,[6,7],result)
        self.assertEqual(candidate[0]["gw6"],6)
        self.assertIn("Official availability",result["players"]["1"]["notes"][0])

    def test_contradictory_fitness_news_withholds_adjustment(self):
        result=self.extract([self.doc("Haaland is ruled out.",kind="official",url="https://www.mancity.com/news/a"),self.doc("Haaland will start.",source="B")])
        apply_evidence(self.players,[6,7],result)
        self.assertEqual(result["players"]["1"]["news_fraction"],0)

    def test_fresh_official_next_match_absence_is_a_constraint(self):
        published=(self.now-timedelta(minutes=20)).isoformat()
        doc=self.doc("Haaland is ruled out for the next match.",kind="official",url="https://www.mancity.com/news/report",published_at=published)
        result=self.extract([doc]);candidate,learned=apply_evidence(self.players,[6,7],result)
        self.assertEqual(candidate[0]['gw6'],0);self.assertEqual(learned[0]['gw6'],0)
        self.assertEqual(candidate[0]['gw7'],7)
        self.assertEqual(result['players']['1']['expected_minutes_proxy'],0)

    def test_old_or_unspecified_official_absence_cannot_force_zero(self):
        for doc in [self.doc("Haaland is ruled out.",kind="official",url="https://www.mancity.com/news/report"),self.doc("Haaland is ruled out for the next match.",kind="official",url="https://www.mancity.com/news/report")]:
            result=self.extract([doc]);candidate,_=apply_evidence(self.players,[6,7],result)
            self.assertGreater(candidate[0]['gw6'],0)

    def test_source_settings_reject_arbitrary_fetch_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            for key in ["http://localhost", "../../secret", "FantasyPL?x=1"]:
                with self.assertRaises(DataError):save_settings(directory,{"sources":[{"kind":"reddit","subreddit":key}]})
            cfg=save_settings(directory,{"sources":[{"kind":"reddit","subreddit":"FantasyPL","name":"FPL"}]})
            self.assertEqual(settings(directory),cfg)

    def test_import_requires_real_dates_and_keeps_text_private(self):
        with tempfile.TemporaryDirectory() as directory:
            payload={"url":"https://www.mancity.com/news/report","published_at":self.now.isoformat(),"text":"Haaland is ruled out for the next match."}
            result=import_text(directory,payload)
            self.assertEqual(result["coverage"],"imported_text")
            self.assertTrue((Path(directory)/"user/qualitative_imports.json").exists())
            with self.assertRaises(DataError):import_text(directory,{**payload,"published_at":"2026-01-01"})
            with self.assertRaises(DataError):import_text(directory,{**payload,"url":"javascript:alert(1)"})

    def test_transcript_access_failure_is_explicit_coverage(self):
        xml=f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"><entry><title>FPL GW6</title><published>{self.now.isoformat()}</published><link href="https://www.youtube.com/watch?v=abcdefghijk"/><yt:videoId>abcdefghijk</yt:videoId></entry></feed>'.encode()
        cfg={"lookback_days":7,"videos_per_channel":2}
        with tempfile.TemporaryDirectory() as directory,patch('fpl_advisor.qual_sources.get',return_value=xml),patch('fpl_advisor.qual_sources.transcript',side_effect=RuntimeError('blocked')):
            docs,status=youtube({"name":"A","channel_id":"UCweDAlFm2LnVcOqaFU4_AGA"},cfg,directory,self.now)
            self.assertEqual(status["status"],"description_only")
            self.assertEqual(status["transcripts"],0)
            self.assertEqual(status["details"][0]["reason"],"RuntimeError")
            self.assertNotIn("segments",docs[0])

    def test_linked_official_article_requires_publication_and_article_body(self):
        page=f'<title>Club update</title><script type="application/ld+json">{{"datePublished":"{self.now.isoformat()}"}}</script><article>Haaland is ruled out for the next match.</article>'.encode()
        with patch('fpl_advisor.qual_sources.get',return_value=page):
            doc=linked_official('https://www.mancity.com/news/report',self.now)
            self.assertEqual(doc['kind'],'official');self.assertIn('Haaland',doc['text'])
        for body in [b'<article>Haaland ruled out.</article>',f'<script>{{"datePublished":"{self.now.isoformat()}"}}</script><nav>Haaland ruled out.</nav>'.encode()]:
            with patch('fpl_advisor.qual_sources.get',return_value=body),self.assertRaises(DataError):
                linked_official('https://www.mancity.com/news/report',self.now)

    def test_later_official_fitness_update_cancels_previous_forced_absence(self):
        docs=[self.doc('Haaland is ruled out for the next match.',kind='official',url='https://www.mancity.com/news/a',published_at=(self.now-timedelta(minutes=30)).isoformat()),
              self.doc('Haaland is cleared to play.',kind='official',url='https://www.mancity.com/news/b',published_at=(self.now-timedelta(minutes=5)).isoformat())]
        result=self.extract(docs);candidate,_=apply_evidence(self.players,[6,7],result)
        self.assertGreater(candidate[0]['gw6'],0);self.assertFalse(result['players']['1']['hard_out'])

    def snapshot(self):
        q=self.extract([self.doc("Haaland is essential.")]);candidate,learned=apply_evidence(self.players,[6,7],q)
        return {"manifest":self.manifest,"players":self.players,"qualitative":q,"qualitative_candidate_players":candidate,"qualitative_learned_players":learned}

    def test_archive_is_paired_and_excludes_raw_text_urls_and_user_names(self):
        with tempfile.TemporaryDirectory() as directory:
            s=self.snapshot();self.assertTrue(archive_pair(directory,s))
            files=list((Path(directory)/'data/qualitative_archive/2026-27').glob('*.gz'))
            body=gzip.decompress(files[0].read_bytes()).decode()
            self.assertNotIn('Creator A',body);self.assertNotIn('https:',body);self.assertNotIn('essential',body)
            self.assertEqual(len(json.loads(body)['rows']),2)
            self.assertFalse(archive_pair(directory,s))

    def test_offline_or_after_deadline_pairs_are_not_archived(self):
        with tempfile.TemporaryDirectory() as directory:
            s=self.snapshot();s['qualitative']['online_capture']=False
            self.assertFalse(archive_pair(directory,s))
            s=self.snapshot();s['manifest']={**self.manifest,'deadline':(self.now-timedelta(hours=1)).isoformat()}
            self.assertFalse(archive_pair(directory,s))

    def test_actuals_match_code_and_finalization_without_zero_filling(self):
        with tempfile.TemporaryDirectory() as directory:
            archive_pair(directory,self.snapshot())
            hist=pd.DataFrame([record(1,6,7,season='2026-27'),record(2,6,4,season='2026-27',code=999)])
            report,rows=evaluate(directory,hist,{**self.manifest,'completed_gameweeks':[6]})
            self.assertEqual(len(rows),1);self.assertEqual(rows.iloc[0].actual,7)
            self.assertEqual(report['recommended_gain'],0)
            _,rows=evaluate(directory,hist,self.manifest);self.assertTrue(rows.empty)

    def test_live_influence_learns_from_earlier_gws_and_rejects_harm(self):
        for benefit in [True,False]:
            with self.subTest(benefit=benefit),tempfile.TemporaryDirectory() as directory:
                hist=[]
                for gw in range(6,10):
                    path=Path(directory)/'data/qualitative_archive/2026-27'/f'gw{gw:02d}.json.gz';path.parent.mkdir(parents=True,exist_ok=True)
                    deadline=self.now-timedelta(days=10-gw)
                    rows=[{'id':i,'code':i+100,'position':'MID','stats':5,'candidate':5.2,'minutes_recent':90,'minutes_proxy':90,'games':1,'signals':[]} for i in range(1,16)]
                    pair={'version':VERSION,'season':'2026-27','gw':gw,'created_at':(deadline-timedelta(hours=3)).isoformat(),'stats_created_at':(deadline-timedelta(hours=4)).isoformat(),'deadline':deadline.isoformat(),'trained_through':season_order('2026-27',gw-1),'used_gain':0,'evidence_fingerprint':'test','rows':rows}
                    path.write_bytes(gzip.compress(json.dumps(pair).encode()))
                    hist.extend(record(i,gw,6 if benefit else 4,season='2026-27') for i in range(1,16))
                report,_=evaluate(directory,pd.DataFrame(hist),{**self.manifest,'completed_gameweeks':[6,7,8,9]})
                self.assertEqual(report['recommended_gain'],1 if benefit else 0)
                self.assertEqual(report['forward_test']['gameweeks'],[8,9])

    def test_two_model_views_preserve_legality_and_stats_mode(self):
        frame=player_pool();rows=frame.to_dict('records')
        for p in rows:
            p.update({'code':p['id']+100,'name':'Player'+str(p['id']),'full_name':'Player'+str(p['id']),'minutes_recent':90,'games6':1,'gw6':6,'gw7':5,'next_points':6,'horizon_points':11,'weighted_points':10})
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);q=evidence({'documents':[self.doc('Player1 is essential.')],'settings':{}},rows,self.manifest,self.now.isoformat())
            candidate,learned=apply_evidence(rows,[6,7],q)
            write_json(root/'config.json',{'horizon':2,'discount':.8,'solver_seconds':5})
            write_json(root/'reports/snapshot.json',{'manifest':self.manifest,'players':rows,'qualitative':q,'qualitative_candidate_players':candidate,'qualitative_learned_players':learned})
            a=selection(root,{'mode':'wildcard','budget':80,'compare_qualitative':True})
            self.assertEqual(len(a['stats_only']['squad']),15);self.assertEqual(len(a['with_qualitative']['squad']),15)
            self.assertEqual(state(root)['players'][0]['gw6'],6)
            with self.assertRaises(DataError):selection(root,{'forecast_view':'unknown'})


if __name__=='__main__':unittest.main()
