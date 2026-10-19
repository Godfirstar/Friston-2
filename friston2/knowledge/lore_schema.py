"""SQLite property graph: statements, source spans and mentions stay distinct."""
DB_VERSION = 'lore_wiki_graph_2'
DDL = '''
PRAGMA foreign_keys=ON;
CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE sources(
 id TEXT PRIMARY KEY,path TEXT NOT NULL,url TEXT NOT NULL,revision TEXT NOT NULL,
 sha256 TEXT NOT NULL,generated_at TEXT NOT NULL,version TEXT NOT NULL,
 source_type TEXT NOT NULL CHECK(source_type IN ('llm_summary','project_config'))
);
CREATE TABLE entities(
 id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,
 scope TEXT NOT NULL CHECK(scope IN ('canon','fan')),
 summary TEXT NOT NULL,source TEXT NOT NULL,source_key TEXT NOT NULL,
 coverage TEXT NOT NULL
);
CREATE TABLE aliases(
 alias_norm TEXT NOT NULL,entity_id TEXT NOT NULL REFERENCES entities(id),
 alias TEXT NOT NULL,origin TEXT NOT NULL,priority INTEGER NOT NULL,
 PRIMARY KEY(alias_norm,entity_id)
);
CREATE INDEX idx_alias_norm ON aliases(alias_norm);
CREATE TABLE passages(
 id INTEGER PRIMARY KEY,kind TEXT NOT NULL,ref_id TEXT NOT NULL REFERENCES entities(id),
 section TEXT NOT NULL,category TEXT NOT NULL,content TEXT NOT NULL,source TEXT NOT NULL,
 scope TEXT NOT NULL,source_id TEXT NOT NULL REFERENCES sources(id),
 line_start INTEGER NOT NULL,line_end INTEGER NOT NULL
);
CREATE INDEX idx_passage_owner ON passages(ref_id,category);
CREATE VIRTUAL TABLE passage_fts USING fts5(tokens,content='',tokenize='unicode61');
CREATE TABLE relations(
 id TEXT PRIMARY KEY,src TEXT NOT NULL REFERENCES entities(id),
 dst TEXT NOT NULL REFERENCES entities(id),relation TEXT NOT NULL,description TEXT NOT NULL,
 scope TEXT NOT NULL,source TEXT NOT NULL,source_id TEXT NOT NULL REFERENCES sources(id),
 passage_id INTEGER NOT NULL REFERENCES passages(id),
 status TEXT NOT NULL CHECK(status IN ('source_asserted','uncertain','project')),
 verified INTEGER NOT NULL DEFAULT 0,target_label TEXT NOT NULL
);
CREATE INDEX idx_rel_src ON relations(src,dst);
CREATE INDEX idx_rel_dst ON relations(dst,src);
CREATE TABLE event_mentions(
 event_id TEXT NOT NULL REFERENCES entities(id),entity_id TEXT NOT NULL REFERENCES entities(id),
 passage_id INTEGER NOT NULL REFERENCES passages(id),basis TEXT NOT NULL,
 PRIMARY KEY(event_id,entity_id,passage_id)
);
CREATE INDEX idx_event_entity ON event_mentions(entity_id,event_id);
CREATE TABLE import_issues(
 id INTEGER PRIMARY KEY,path TEXT NOT NULL,reason TEXT NOT NULL,detail TEXT NOT NULL
);
'''
