"""Relationship records for review (Day 11 hardening). A relation is NOT an identity decision.

It says "these two records are related, or share a posting under different labels, or this label is not a real company",
with the evidence and the rule version. Nothing in the application reads a relation to merge, close, hide or re-rank a
record; it exists so a person (and, later, a presentation layer) can see the relationship without it being mistaken for
sameness. Candidate relations are never promoted to 'verified' by code.
"""

import json

from . import database as db
from .identity import RULE_VERSION, table_exists

KINDS = ("related_company", "same_posting_different_label", "same_requisition_different_posting", "invalid_source_label")


def record_relation(conn, entity_type, kind, left_id, right_id, *, detail, rule, evidence, decided_by="rule:identity_review",
                    status="candidate", review_status="unreviewed", now=None):
    """Create a relation once; repeating the same call changes nothing. Returns (relation_id, created)."""
    if kind not in KINDS:
        raise ValueError(f"unknown relation kind {kind}")
    if not table_exists(conn, "identity_relations"):
        raise RuntimeError("identity_relations does not exist yet (needs schema version 6)")
    now = now or db.utc_now()
    if right_id is not None and left_id > right_id:
        left_id, right_id = right_id, left_id             # a relation has no direction: store one canonical order
    with db.transaction(conn):
        row = conn.execute("""SELECT relation_id FROM identity_relations WHERE entity_type = ? AND kind = ? AND left_id = ?
                              AND COALESCE(right_id, '') = ?""", (entity_type, kind, left_id, right_id or "")).fetchone()
        if row:
            return row["relation_id"], False
        relation_id = db.new_id()
        conn.execute("""INSERT INTO identity_relations (relation_id, entity_type, kind, left_id, right_id, status, detail, rule,
                                                         rule_version, evidence, review_status, decided_by, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (relation_id, entity_type, kind, left_id, right_id, status, detail, rule, RULE_VERSION,
                      json.dumps(evidence, sort_keys=True), review_status, decided_by, now, now))
        return relation_id, True


def list_relations(conn, entity_type=None, kind=None):
    if not table_exists(conn, "identity_relations"):
        return []
    sql, args = "SELECT * FROM identity_relations WHERE 1 = 1", []
    if entity_type:
        sql += " AND entity_type = ?"
        args.append(entity_type)
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    return conn.execute(sql + " ORDER BY created_at, relation_id", args).fetchall()
