"""Farm review regressions: locking, membership, relationships and PHI output."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]


def test_optimistic_lock_precheck_keeps_microsecond_precision() -> None:
    sys.path.insert(0, str(BACKEND))
    from app.routers.farm import _surum_dogrula

    current = {"updated_at": datetime(2026, 8, 8, 10, 0, 0, 123456, tzinfo=timezone.utc)}
    with pytest.raises(HTTPException) as exc:
        _surum_dogrula(
            current, datetime(2026, 8, 8, 10, 0, 0, 123455, tzinfo=timezone.utc)
        )
    assert exc.value.status_code == 409


def run_review_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE], cwd=BACKEND, env=env,
        capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_farm_review_fixes_sqlite(tmp_path: Path) -> None:
    run_review_smoke(f"sqlite:///{(tmp_path / 'farm-review.db').as_posix()}")


_SMOKE = r'''
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal, get_db
from app.main import app
from app.routers import farm as farm_router


def login(client):
    response = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
    assert response.status_code == 200, response.text
    body = response.json()
    headers = {'Authorization':'Bearer '+body['access_token'],
               'X-Company-ID':str(body['companies'][0]['id'])}
    changed = client.post('/api/auth/change-password', headers=headers,
                          json={'current_password':'admin123','new_password':'FarmReview!123'})
    assert changed.status_code == 200, changed.text
    headers['Authorization'] = 'Bearer '+changed.json()['access_token']
    return headers


def add_user(company_id, username, active):
    with SessionLocal() as db:
        uid = int(db.execute(text("""
            INSERT INTO app_users(username,email,email_verified,display_name,password_hash,
              role,is_active,must_change_password,created_at)
            VALUES(:u,:e,:verified,:d,:p,'teknisyen',:active,:must_change,CURRENT_TIMESTAMP)
            RETURNING id"""), {'u':username,'e':username+'@example.invalid','verified':True,
              'd':username,'p':'not-used','active':active,'must_change':False}).scalar_one())
        db.execute(text("""INSERT INTO user_company_memberships
          (user_id,company_id,is_default,created_at)
          VALUES(:uid,:cid,:is_default,CURRENT_TIMESTAMP)"""),
          {'uid':uid,'cid':company_id,'is_default':False})
        db.commit()
        return uid


with TestClient(app) as client:
    h = login(client)
    company_a = int(h['X-Company-ID'])
    company_b_response = client.post('/api/companies', headers=h, json={'name':'Review B'})
    assert company_b_response.status_code == 201, company_b_response.text
    company_b = int(company_b_response.json()['id'])

    active_member = add_user(company_a, 'farm-active-member', True)
    inactive_member = add_user(company_a, 'farm-inactive-member', False)
    other_member = add_user(company_b, 'farm-other-member', True)

    farm = client.post('/api/farms', headers=h, json={'code':'R1','name':'Review Farm'}).json()

    # A stale writer with the same whole second but older microseconds must
    # receive 409 and must not overwrite the winning update.
    stale_version = datetime(2026, 8, 8, 12, 0, 0, 123456, tzinfo=timezone.utc)
    winning_version = datetime(2026, 8, 8, 12, 0, 0, 654321, tzinfo=timezone.utc)
    with SessionLocal() as db:
        db.execute(text("""UPDATE farms SET updated_at=:updated_at
          WHERE id=:farm_id AND company_id=:company_id"""), {
          'updated_at':stale_version, 'farm_id':farm['id'], 'company_id':company_a})
        db.commit()

    stale_snapshot = client.get(f"/api/farms/{farm['id']}", headers=h)
    assert stale_snapshot.status_code == 200, stale_snapshot.text
    stale_snapshot = stale_snapshot.json()
    assert datetime.fromisoformat(stale_snapshot['updated_at']).microsecond == 123456

    original_now = farm_router._simdi
    farm_router._simdi = lambda: winning_version
    winning_update = client.put(f"/api/farms/{farm['id']}", headers=h, json={
      'code':farm['code'], 'name':'Winning Farm', 'status':'ACTIVE',
      'expected_updated_at':stale_snapshot['updated_at']})
    farm_router._simdi = original_now
    assert winning_update.status_code == 200, winning_update.text
    winning_update = winning_update.json()
    assert winning_update['name'] == 'Winning Farm'
    assert datetime.fromisoformat(winning_update['updated_at']).microsecond == 654321

    concurrent_version = datetime(2026, 8, 8, 12, 0, 0, 777777, tzinfo=timezone.utc)
    race = {'triggered':False}

    class RaceSession:
        def __init__(self, session):
            self._session = session

        def execute(self, statement, params=None, *args, **kwargs):
            is_farm_cas = (
                not race['triggered']
                and isinstance(params, dict)
                and 'expected_updated_at' in params
                and 'UPDATE farms' in str(statement)
            )
            if is_farm_cas:
                race['triggered'] = True
                self._session.execute(text("""UPDATE farms
                  SET name=:name, updated_at=:updated_at
                  WHERE id=:farm_id AND company_id=:company_id"""), {
                  'name':'Concurrent Winner', 'updated_at':concurrent_version,
                  'farm_id':farm['id'], 'company_id':company_a})
                self._session.commit()
                return type('ZeroRowResult', (), {'rowcount':0})()
            return self._session.execute(statement, params, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._session, name)

    def race_db():
        with SessionLocal() as session:
            yield RaceSession(session)

    app.dependency_overrides[get_db] = race_db
    try:
        stale_update = client.put(f"/api/farms/{farm['id']}", headers=h, json={
          'code':farm['code'], 'name':'Stale overwrite', 'status':'ACTIVE',
          'expected_updated_at':winning_update['updated_at']})
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert race['triggered'], 'conditional farm UPDATE was not reached after precheck'
    assert stale_update.status_code == 409, stale_update.text

    persisted_farm = client.get(f"/api/farms/{farm['id']}", headers=h)
    assert persisted_farm.status_code == 200, persisted_farm.text
    persisted_farm = persisted_farm.json()
    assert persisted_farm['name'] == 'Concurrent Winner'
    assert datetime.fromisoformat(persisted_farm['updated_at']).microsecond == 777777

    parcel_1 = client.post('/api/farm-parcels', headers=h, json={
      'farm_id':farm['id'],'code':'RP1','name':'Parcel One','area_decare':'40.0000'}).json()
    parcel_2 = client.post('/api/farm-parcels', headers=h, json={
      'farm_id':farm['id'],'code':'RP2','name':'Parcel Two','area_decare':'30.0000'}).json()
    season_1 = client.post('/api/crop-seasons', headers=h, json={
      'parcel_id':parcel_1['id'],'season_year':2026,'crop':'Wheat',
      'started_on':'2026-03-01','planted_area_decare':'40.0000'}).json()
    season_2 = client.post('/api/crop-seasons', headers=h, json={
      'parcel_id':parcel_2['id'],'season_year':2026,'crop':'Barley',
      'started_on':'2026-03-01','planted_area_decare':'30.0000'}).json()

    base_activity = {'season_id':season_1['id'],'activity_type':'OTHER',
                     'performed_at':'2026-05-01T08:00:00+00:00'}
    for invalid_user in (inactive_member, other_member):
        response = client.post('/api/field-activities', headers=h,
                               json={**base_activity,'operator_user_id':invalid_user})
        assert response.status_code == 400, response.text
    activity = client.post('/api/field-activities', headers=h, json={
      **base_activity,'operator_user_id':active_member,'preharvest_interval_days':30})
    assert activity.status_code == 201, activity.text
    activity = activity.json()

    invalid_task = client.post('/api/field-tasks', headers=h, json={
      'title':'Wrong assignee','assigned_user_id':other_member})
    assert invalid_task.status_code == 400, invalid_task.text
    mismatch = client.post('/api/field-tasks', headers=h, json={
      'title':'Wrong parcel','season_id':season_1['id'],'parcel_id':parcel_2['id']})
    assert mismatch.status_code == 422, mismatch.text

    task_response = client.post('/api/field-tasks', headers=h, json={
      'title':'Consistent task','season_id':season_1['id'],
      'assigned_user_id':active_member})
    assert task_response.status_code == 201, task_response.text
    task = task_response.json()
    assert task['parcel_id'] == parcel_1['id'], task

    mismatch_update = client.put(f"/api/field-tasks/{task['id']}", headers=h, json={
      'title':'Mismatch','season_id':season_2['id'],'parcel_id':parcel_2['id'],
      'activity_id':activity['id'],'assigned_user_id':active_member,
      'expected_updated_at':task['updated_at']})
    assert mismatch_update.status_code == 422, mismatch_update.text
    invalid_update = client.put(f"/api/field-tasks/{task['id']}", headers=h, json={
      'title':'Invalid user','season_id':season_1['id'],'parcel_id':parcel_1['id'],
      'assigned_user_id':inactive_member,'expected_updated_at':task['updated_at']})
    assert invalid_update.status_code == 400, invalid_update.text

    decision = client.get('/api/field-harvest-decision', headers=h, params={
      'season_id':season_1['id'],'harvested_on':'2026-05-15'})
    assert decision.status_code == 200, decision.text
    decision_body = decision.json()
    assert decision_body['decision'] == 'reason_required', decision_body
    assert decision_body['safe_from'] == '2026-05-31', decision_body

    harvest = client.post('/api/field-harvests', headers=h, json={
      'season_id':season_1['id'],'harvested_on':'2026-05-15','quantity':'100.0000',
      'unit':'KG','sold_quantity':'25.5000','revenue_amount':'1234.50',
      'safety_override_reason':'Historical record checked'})
    assert harvest.status_code == 201, harvest.text
    created = harvest.json()
    assert created['sold_quantity'] == '25.5000', created
    assert created['revenue_amount'] == '1234.50', created

    null_harvest = client.post('/api/field-harvests', headers=h, json={
      'season_id':season_1['id'],'harvested_on':'2026-06-01',
      'quantity':'10.0000','unit':'KG'})
    assert null_harvest.status_code == 201, null_harvest.text
    assert null_harvest.json()['sold_quantity'] is None, null_harvest.text
    assert null_harvest.json()['revenue_amount'] is None, null_harvest.text

    listed_items = client.get('/api/field-harvests', headers=h).json()['items']
    listed = next(row for row in listed_items if row['id'] == created['id'])
    for key in ('sold_quantity','revenue_amount','safety_override_reason','safety_warning'):
        assert key in listed, (key, listed)
    assert listed['safety_override_reason'] == 'Historical record checked', listed
    assert listed['safety_warning'], listed
    assert listed['sold_quantity'] == created['sold_quantity'] == '25.5000', listed
    assert listed['revenue_amount'] == created['revenue_amount'] == '1234.50', listed
    listed_null = next(row for row in listed_items if row['id'] == null_harvest.json()['id'])
    assert listed_null['sold_quantity'] is None, listed_null
    assert listed_null['revenue_amount'] is None, listed_null

    timeline = client.get(f"/api/farm-parcels/{parcel_1['id']}/timeline", headers=h)
    assert timeline.status_code == 200, timeline.text
    detail = next(row for row in timeline.json()['harvests'] if row['id'] == created['id'])
    assert detail['sold_quantity'] == '25.5000', detail
    assert detail['revenue_amount'] == '1234.50', detail

print('FARM REVIEW FIXES OK')
'''
