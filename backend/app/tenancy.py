from __future__ import annotations
import re
from fastapi import HTTPException, Request
from sqlalchemy import MetaData, Table, Column, Integer, String, Text, Boolean, DateTime, ForeignKey, select, insert, text, true, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import TextClause
from .auth import utcnow
from .core_schema import _make_company_id_required
metadata=MetaData()
companies=Table('companies',metadata,
 Column('id',Integer,primary_key=True,autoincrement=True),Column('name',String(200),nullable=False),Column('tax_number',String(40)),Column('is_active',Boolean,nullable=False,default=True),Column('negative_stock_policy',String(30),nullable=False,default='block',server_default='block'),Column('credit_limit_policy',String(30),nullable=False,default='block',server_default='block'),Column('farm_area_override_policy',String(20),nullable=False,default='require_reason',server_default='require_reason'),Column('farm_early_harvest_policy',String(20),nullable=False,default='require_reason',server_default='require_reason'),Column('farm_spraying_dose_required',Boolean,nullable=False,default=True,server_default=true()),Column('farm_monoculture_policy',String(20),nullable=False,default='require_reason',server_default='require_reason'),Column('farm_reentry_policy',String(20),nullable=False,default='require_reason',server_default='require_reason'),Column('farm_plantback_policy',String(20),nullable=False,default='require_reason',server_default='require_reason'),Column('herd_withdrawal_policy',String(20),nullable=False,default='require_reason',server_default='require_reason'),Column('profiller',Text,nullable=False,default='',server_default=''),Column('created_at',DateTime(timezone=True),nullable=False))
branches=Table('branches',metadata,
 Column('id',Integer,primary_key=True,autoincrement=True),Column('company_id',Integer,ForeignKey('companies.id'),nullable=False,index=True),Column('name',String(160),nullable=False),Column('is_active',Boolean,nullable=False,default=True),Column('created_at',DateTime(timezone=True),nullable=False))
memberships=Table('user_company_memberships',metadata,
 Column('id',Integer,primary_key=True,autoincrement=True),Column('user_id',Integer,nullable=False,index=True),Column('company_id',Integer,nullable=False,index=True),Column('is_default',Boolean,nullable=False,default=False),Column('created_at',DateTime(timezone=True),nullable=False))
CORE_TABLES=('customers','suppliers','products','orders','purchases','payments','stock_movements')

_SQL_CLAUSE_KEYWORD = re.compile(
 r"\b(?:select|from|join|on|where|having|set|returning|limit|offset|union|"
 r"group\s+by|order\s+by)\b",
 re.IGNORECASE,
)


def _parenthesis_metadata(sql: str) -> tuple[list[int], dict[int, int]]:
 depths: list[int] = []
 stack: list[int] = []
 pairs: dict[int, int] = {}
 depth = 0
 for index, char in enumerate(sql):
  depths.append(depth)
  if char == "(":
   stack.append(index)
   depth += 1
  elif char == ")" and stack:
   depth -= 1
   pairs[stack.pop()] = index
 depths.append(depth)
 return depths, pairs


def tenant_predicate_is_required(sql: str, start: int, end: int) -> bool:
 """Return whether a tenant equality is a required conjunct of its SQL clause."""

 depths, pairs = _parenthesis_metadata(sql)
 clauses = list(_SQL_CLAUSE_KEYWORD.finditer(sql))
 contexts = [clause for clause in clauses if clause.start() < start]
 if not contexts:
  return False
 context = contexts[-1]
 context_name = " ".join(context.group(0).lower().split())
 if context_name not in {"on", "where"}:
  return False

 context_depth = depths[context.start()]
 clause_end = len(sql)
 if context_depth:
  for index in range(end, len(sql)):
   if sql[index] == ")" and depths[index] == context_depth:
    clause_end = index
    break
 for clause in clauses:
  if clause.start() <= end:
   continue
  if clause.start() < clause_end and depths[clause.start()] == context_depth:
   clause_end = clause.start()
   break

 direct_after = re.sub(r"^\s*\)+", "", sql[end:clause_end])
 if re.match(r"\s*(?:or\b|is\b|[<>=!])", direct_after, re.IGNORECASE):
  return False

 for operator in re.finditer(r"\bor\b", sql[context.end():clause_end], re.IGNORECASE):
  operator_start = context.end() + operator.start()
  low, high = sorted((start, operator_start))
  if depths[operator_start] <= min(depths[low:high + 1]):
   return False

 for operator in re.finditer(r"\bnot\b", sql[context.end():start], re.IGNORECASE):
  operator_end = context.end() + operator.end()
  between = sql[operator_end:start]
  if re.fullmatch(r"\s*(?:\(\s*)*", between):
   return False
  opening = operator_end
  while opening < start and sql[opening].isspace():
   opening += 1
  if opening < start and sql[opening] == "(" and pairs.get(opening, -1) >= end:
   return False

 for opening, closing in pairs.items():
  if context.end() <= opening < start and closing >= end:
   suffix = sql[closing + 1:clause_end]
   if re.match(r"\s*is\s+(?:false|not\s+true)\b", suffix, re.IGNORECASE):
    return False
 return True


def initialize_tenancy(engine:Engine)->None:
 metadata.create_all(engine)
 with engine.begin() as conn:
  company=conn.execute(select(companies.c.id).limit(1)).first()
  if not company:
   company_id=conn.execute(insert(companies).values(name='Ana Firma',is_active=True,created_at=utcnow())).inserted_primary_key[0]
   conn.execute(insert(branches).values(company_id=company_id,name='Merkez',is_active=True,created_at=utcnow()))
  else: company_id=company[0]
  inspector=inspect(conn)
  for table in CORE_TABLES:
   if not inspector.has_table(table): continue
   cols={x['name'] for x in inspector.get_columns(table)}
   if 'company_id' not in cols:
     conn.execute(text(f'ALTER TABLE {table} ADD COLUMN company_id INTEGER'))
   conn.execute(text(f'UPDATE {table} SET company_id=:cid WHERE company_id IS NULL'),{'cid':company_id})
   _make_company_id_required(conn, table)
  inspector=inspect(conn)
  for table in ('orders','purchases','payments'):
   if inspector.has_table(table) and 'branch_id' not in {x['name'] for x in inspector.get_columns(table)}: conn.execute(text(f'ALTER TABLE {table} ADD COLUMN branch_id INTEGER'))
  users=conn.execute(text('SELECT id FROM app_users')).fetchall()
  for (uid,) in users:
   if not conn.execute(select(memberships.c.id).where(memberships.c.user_id==uid,memberships.c.company_id==company_id)).first():
    conn.execute(insert(memberships).values(user_id=uid,company_id=company_id,is_default=True,created_at=utcnow()))

def user_companies(db:Session,user_id:int):
 rows=db.execute(select(companies.c.id,companies.c.name,companies.c.tax_number,companies.c.negative_stock_policy,companies.c.credit_limit_policy,memberships.c.is_default).join(memberships,memberships.c.company_id==companies.c.id).where(memberships.c.user_id==user_id,companies.c.is_active==True).order_by(memberships.c.is_default.desc(),companies.c.name)).mappings().all()
 return [dict(x) for x in rows]

def resolve_company(db:Session,user_id:int,requested:int|None)->int:
 query=(
  select(memberships.c.company_id)
  .join(companies, companies.c.id == memberships.c.company_id)
  .where(
   memberships.c.user_id==user_id,
   companies.c.is_active.is_(True),
  )
 )
 # A requested company is always applied as a strict membership and active-company
 # filter. A non-member or inactive selection yields no row and fails closed with
 # the same 403 response, avoiding tenant lifecycle state disclosure.
 if requested is not None: query=query.where(memberships.c.company_id==requested)
 else: query=query.order_by(memberships.c.is_default.desc(),memberships.c.id)
 row=db.execute(query.limit(1)).first()
 if not row: raise HTTPException(403,'Bu firmaya erişim yetkiniz yok')
 return int(row[0])

def company_id(request:Request)->int:
 cid=getattr(request.state,'company_id',None)
 if not cid: raise HTTPException(400,'Aktif firma seçilmedi')
 return int(cid)


def tenant_text(statement: str, company_id: int) -> tuple[TextClause, dict[str, int]]:
 """Build raw SQL only when it carries the canonical tenant bind predicate.

 This deliberately narrow helper keeps validation, the SQL object and its
 ``cid`` bind value together. Dynamic query builders cannot accidentally omit
 ``company_id = :cid`` while still receiving executable SQL from this helper.
 """

 sql_structure = re.sub(
  r"(?P<dollar>\$(?:[A-Za-z_]\w*)?\$).*?(?P=dollar)"
  r"|--[^\r\n]*|/\*.*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\""
  r"|`(?:``|[^`])*`|\[(?:\]\]|[^\]])*\]",
  " ",
  statement,
  flags=re.DOTALL,
 )
 scope_match = re.match(
  r"^\s*(?:select|update|delete)\b"
  r"(?:(?!\b(?:select|where)\b).)*\bwhere\s+"
  r"(?:\(\s*)*(?P<tenant>(?:[a-z_]\w*\.)?company_id\s*=\s*:cid\b)"
  r"(?:\s*\))*\s*(?:$|\band\b|\bgroup\b|\border\b|\blimit\b|\boffset\b)",
  sql_structure,
  re.IGNORECASE | re.DOTALL,
 )
 if scope_match is None or not tenant_predicate_is_required(
  sql_structure,
  scope_match.start("tenant"),
  scope_match.end("tenant"),
 ):
  raise ValueError("Tenant SQL must include company_id = :cid")
 return text(statement), {"cid": int(company_id)}
