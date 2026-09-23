// Run with: npm test   (uses Node's built-in test runner, no extra packages)
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const app = require("../server");

let server;
let base;
const auth = { headers: { Authorization: "Bearer dev-key" } };

before(async () => {
  server = app.listen(0);
  await new Promise((r) => server.once("listening", r));
  base = `http://127.0.0.1:${server.address().port}`;
});
after(() => server.close());

const get = (path, opts = auth) => fetch(base + path, opts).then(async (r) => ({ status: r.status, body: await r.json() }));

test("health needs no key", async () => {
  const r = await get("/health", {});
  assert.equal(r.status, 200);
  assert.equal(r.body.students, 40);
});

test("API key is required", async () => {
  const r = await get("/api/v1/students/search?last_name=Kulkarni", {});
  assert.equal(r.status, 401);
});

test("search by full name, case-insensitive", async () => {
  const r = await get("/api/v1/students/search?first_name=priya&last_name=KULKARNI");
  assert.equal(r.body.count, 1);
  assert.equal(r.body.results[0].roll_number, 27);
});

test("duplicate names come back together", async () => {
  const r = await get("/api/v1/students/search?first_name=Aarav&last_name=Deshmukh");
  assert.deepEqual(r.body.results.map((s) => `${s.class}-${s.division}`).sort(), ["6-C", "8-A"]);
});

test("search by class, division and roll number", async () => {
  const r = await get("/api/v1/students/search?class=8&division=a&roll_number=12");
  assert.equal(r.body.count, 1);
  assert.equal(r.body.results[0].first_name, "Aarav");
});

test("first name within a class", async () => {
  const r = await get("/api/v1/students/search?first_name=Aarav&class=8&division=A");
  assert.equal(r.body.count, 1);
});

test("names are exact: misheard surname finds nothing", async () => {
  const r = await get("/api/v1/students/search?last_name=Desmukh");
  assert.equal(r.body.count, 0);
});

test("validation errors", async () => {
  assert.equal((await get("/api/v1/students/search")).status, 400);
  assert.equal((await get("/api/v1/students/search?class=8&roll_number=twelve")).status, 400);
});

test("get one student by id", async () => {
  assert.equal((await get("/api/v1/students/3")).body.data.first_name, "Priya");
  assert.equal((await get("/api/v1/students/999")).status, 404);
});
