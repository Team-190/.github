// In-memory PostgreSQL only. No HTTP, Onshape, Supabase or backend credentials.
// npm install --no-save --package-lock=false @electric-sql/pglite@0.3.14
// node tests/engineering-sync.test.mjs
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
const { PGlite } = await import(process.env.PGLITE_MODULE || '@electric-sql/pglite');
const db = new PGlite();
const sql = async (text, values = []) => (await db.query(text, values)).rows;
const read = path => readFile(new URL(path, import.meta.url), 'utf8');
await db.exec(`
  create role anon; create role authenticated; create role service_role;
  create schema frc190_baserow_stage;
  create table frc190_baserow_stage.snapshots(id uuid primary key);
  create schema storage;
  create table storage.buckets(id text primary key, public boolean not null);
  create table storage.objects(bucket_id text, name text);
  insert into storage.buckets values('manufacturing-files',false);
`);
await db.exec(await read('./fixtures/contract-normalized.sql'));
await db.exec(await read('./fixtures/contract-attachments.sql'));
await db.exec(await read('../supabase/production/20260906_onshape_engineering_sync.sql'));

const begin = async () => {
  const id = randomUUID();
  await sql('select public.manufacturing_begin_engineering_sync($1,$2)', [id, 'https://example.test/run']);
  return id;
};
const apply = async (payload, id) =>
  (await sql('select public.manufacturing_apply_engineering_sync($1,$2) result', [id || await begin(), payload]))[0].result;
const snapshot = async () => {
  const data = {};
  for (const table of ['assemblies','parts','requirements','operations','finishing','attachments'])
    data[table] = await sql(`select * from manufacturing.${table} order by id`);
  return data;
};
function payload(root = 'A-ONE', revision = 'A') {
  const key = `${root}|${revision}|${root}|P-ONE|default`;
  return {
    assemblies: [{assembly_number:root,subsystem_name:'Subsystem',active:true,
      sync_schema_version:'supabase-engineering-v1',latest_released_revision:revision,
      discovery_master:'https://example.test/master',integration_status:'Discovered — Master Unreleased'}],
    parts: [{part_number:'P-ONE',name:'Plate',revision:'A',active:true}],
    requirements: [{production_key:key,part_number:'P-ONE',assembly_number:root,
      source_root:root,source_assembly_revision:revision,required_part_revision:'A',
      configuration:'default',required_quantity:4,finishing:'Red',machine_op1:'Haas CNC',active_in_bom:true}],
    operations: [{operation_key:`${key}|OP1`,production_key:key,operation_number:'OP1',
      machine:'Haas CNC',work_type:'Manufacturing',active_in_routing:true}],
    finishing: [{production_key:key,color:'Red',required_quantity:4,active:true}],
    attachments: [], synced_roots:[root],discovered_roots:[root],discovery_master:'',
    discovery_complete:true,cad_synced:false,warnings:[],source_rows:1,file_groups_cached:0,
  };
}

// Exercise the API using its actual restricted role.
await db.exec('set role service_role');
await assert.rejects(sql('select * from manufacturing.parts'), /permission denied/);
const first = payload();
const initialResult = await apply(first);
assert.equal(initialResult.status, 'success', JSON.stringify(initialResult));
await db.exec('reset role');
const created = await snapshot();
assert.equal(created.requirements[0].part_id, created.parts[0].id);
assert.equal(created.requirements[0].assembly_id, created.assemblies[0].id);
assert.equal(created.operations[0].requirement_id, created.requirements[0].id);
assert.equal(created.finishing[0].requirement_id, created.requirements[0].id);

// Seed every shop-owned column from model.ts, plus normalized location fields.
const model = await read('./fixtures/contract-model.ts');
const shopByTable = {};
for (const section of model.split('{ name: ').slice(1)) {
  const match = section.match(/^"(\w+)"/);
  if (!match) continue;
  const table = match[1];
  shopByTable[table] = [...section.matchAll(/\["(\w+)","[^"]+","(\w+)","shop"\]/g)]
    .map(([, name, kind]) => [name, kind]);
}
await db.exec("insert into manufacturing.locations(location_key,name) values('L','Shelf')");
for (const [table, fields] of Object.entries(shopByTable)) {
  for (const [name, kind] of fields) {
    const value = kind === 'number' ? 7 : kind === 'date' ? '2026-09-01T00:00:00Z' : 'Shop-owned sentinel';
    await sql(`update manufacturing.${table} set ${name}=$1`, [value]);
  }
  await db.exec(`update manufacturing.${table} set location_id=1`);
}
await db.exec(`update manufacturing.requirements set part_location='On Robot',
  location_updated_by='shop-user',location_updated_at='2026-09-01T00:00:00Z'`);
await db.exec(`insert into manufacturing.operation_allocations
  select id,1,'shop-user','Shop',4,2,'{"untouched":true}' from manufacturing.operations`);
const shopBefore = await snapshot();
const allocations = await sql('select * from manufacturing.operation_allocations');
first.parts[0].name = 'Revised engineering name';
first.requirements[0].required_quantity = 9;
first.finishing[0].required_quantity = 9;
assert.equal((await apply(first)).status, 'success');
const shopAfter = await snapshot();
for (const [table, fields] of Object.entries(shopByTable)) {
  for (const name of [...fields.map(([name])=>name),'location_id',
    ...(table === 'requirements' ? ['part_location','location_updated_by','location_updated_at'] : [])])
    assert.deepEqual(shopAfter[table][0][name], shopBefore[table][0][name], `${table}.${name}`);
}
assert.deepEqual(await sql('select * from manufacturing.operation_allocations'), allocations);
assert.equal(shopAfter.requirements[0].engineering_changed,true);
assert.equal(shopAfter.parts[0].name,'Revised engineering name');

// Every shop column is rejected by the server as well as omitted by the client.
for (const [table, fields] of Object.entries(shopByTable)) {
  for (const [field] of fields) {
    const bad = structuredClone(first); bad[table][0][field]='forbidden';
    const before=await snapshot();
    assert.equal((await apply(bad)).status,'failed', `${table}.${field}`);
    assert.deepEqual(await snapshot(),before);
  }
}

// A late FK failure must roll back earlier assembly/part upserts, including markers.
let bad=structuredClone(first);
bad.parts[0].name='MUST ROLL BACK';
bad.operations[0].production_key='missing';
let before=await snapshot();
assert.equal((await apply(bad)).status,'failed');
assert.deepEqual(await snapshot(),before);
assert.equal((await sql("select status from manufacturing.engineering_sync_runs order by started_at desc limit 1"))[0].status,'failed');

// Shared part across two roots: only the successfully synced root loses old rows.
assert.equal((await apply(payload('A-TWO'))).status,'success');
const secondRootBefore = (await snapshot());
const next=payload('A-ONE','B');
next.warnings=['A-TWO could not be synced and was skipped'];
next.discovery_complete=false;
next.discovery_master='https://example.test/master';
assert.equal((await apply(next)).status,'partial');
const scoped=await snapshot();
const oldRequirement=scoped.requirements.find(r=>r.production_key===first.requirements[0].production_key);
assert.equal(oldRequirement.active_in_bom,false);
assert.equal(scoped.operations.find(o=>o.requirement_id===oldRequirement.id).active_in_routing,false);
assert.equal(scoped.finishing.find(f=>f.requirement_id===oldRequirement.id).active,false);
for (const table of ['requirements','operations','finishing']) {
  const other = secondRootBefore[table].at(-1);
  assert.deepEqual(scoped[table].find(r=>r.id===other.id),other);
}
assert.equal(scoped.assemblies.find(a=>a.assembly_number==='A-TWO').integration_status,'Discovered — Master Unreleased');

// Membership-only reconciliation never mutates work, even for a missing root.
const membership=payload();
for (const table of ['assemblies','parts','requirements','operations','finishing']) membership[table]=[];
membership.synced_roots=[];
membership.discovered_roots=['A-ONE'];
membership.discovery_master='https://example.test/master';
let workBefore=await snapshot();
assert.equal((await apply(membership)).status,'success');
let workAfter=await snapshot();
for (const table of ['parts','requirements','operations','finishing','attachments'])
  assert.deepEqual(workAfter[table],workBefore[table]);
assert.equal(workAfter.assemblies.find(a=>a.assembly_number==='A-TWO').integration_status,'Missing from Main — Review');
membership.discovered_roots=['A-ONE','A-TWO'];
assert.equal((await apply(membership)).status,'success');
assert.equal((await snapshot()).assemblies.find(a=>a.assembly_number==='A-TWO').integration_status,'Discovered — Master Unreleased');

// Empty released BOM legitimately deactivates the successful root's prior work.
const empty=payload('A-ONE','C');
for (const table of ['parts','requirements','operations','finishing']) empty[table]=[];
assert.equal((await apply(empty)).status,'success');
assert.equal((await sql("select count(*)::int n from manufacturing.requirements where source_root='A-ONE' and active_in_bom"))[0].n,0);
bad=structuredClone(empty);bad.synced_roots=[];
assert.equal((await apply(bad)).status,'failed');

// Private attachment registration, replacement, retries and truncating obsolete positions.
const withFiles=payload('A-ONE','D');
const file = digit => ({original_name:'plate.step',content_type:'application/step',byte_size:4,
  sha256:digit.repeat(64),storage_bucket:'manufacturing-files',storage_path:`sha256/${digit.repeat(2)}/${digit.repeat(64)}.step`,
  source_url:`https://cad.onshape.com/export/${digit}`,source_metadata:{export_key:digit},verified_at:'2026-09-06T00:00:00Z'});
for (const digit of ['a','b','c']) await sql('insert into storage.objects values($1,$2)', ['manufacturing-files',file(digit).storage_path]);
withFiles.attachments=[{part_number:'P-ONE',kind:'step',export_key:'a'.repeat(64),files:[file('a'),file('b')]}];
withFiles.cad_synced=true;
assert.equal((await apply(withFiles)).status,'success');
const attachmentIds=(await snapshot()).attachments.map(a=>a.id);
assert.equal((await apply(withFiles)).status,'success');
assert.deepEqual((await snapshot()).attachments.map(a=>a.id),attachmentIds);
withFiles.attachments[0]={part_number:'P-ONE',kind:'step',export_key:'c'.repeat(64),files:[file('c')]};
assert.equal((await apply(withFiles)).status,'success');
assert.equal((await snapshot()).attachments.length,1);
assert.equal((await snapshot()).attachments[0].sha256,'c'.repeat(64));
const apiFile=(await sql("select public.manufacturing_file_for_requirement($1,'step') result",[(await snapshot()).requirements.at(-1).id]))[0].result;
assert.equal(apiFile.path,file('c').storage_path);
bad=structuredClone(withFiles);bad.attachments[0].files[0].storage_path='wrong';
before=await snapshot();assert.equal((await apply(bad)).status,'failed');assert.deepEqual(await snapshot(),before);
await db.exec("update storage.buckets set public=true");
assert.equal((await apply(withFiles)).status,'failed');
await db.exec("update storage.buckets set public=false");

// Retrying an acknowledged or uncertain commit is idempotent; an older run cannot overwrite it.
const older=await begin(), newer=await begin();
assert.equal((await apply(withFiles,newer)).status,'success');
before=await snapshot();
assert.equal((await apply(withFiles,newer)).status,'success');
await sql("select public.manufacturing_finish_engineering_sync($1,'failed','{}')",[newer]);
assert.equal((await sql('select status from manufacturing.engineering_sync_runs where id=$1',[newer]))[0].status,'success');
assert.equal((await apply(withFiles,older)).status,'failed');
assert.deepEqual(await snapshot(),before);

for (const role of ['anon','authenticated']) {
  await db.exec(`set role ${role}`);
  await assert.rejects(sql('select public.manufacturing_engineering_sync_state()'), /permission denied/);
  await assert.rejects(sql('select public.manufacturing_apply_engineering_sync($1,$2)',[randomUUID(),first]), /permission denied/);
  await db.exec('reset role');
}
await db.close();
console.log('PASS: exact contract migration, relationships, all shop fields, scoped deactivation, rollback, attachments, retries, concurrency and grants');
