// Run one rigid-body scenario in Rapier and print the trajectories as JSON.
//
//   node driver.mjs < scenario.json > result.json
//
// Units are SI (m, kg, s, N) and axes are ISO 8855 (z up): the scenario sets
// gravity, so nothing here assumes Rapier's y-up habit. Scenario fields:
//   dt, steps, gravity [x,y,z], solver_iterations (optional), record_every (optional)
//   boxes: [{center, half_extents, friction}]                fixed colliders (ground, steps)
//   bodies: [{id, mass, inertia [Ixx,Iyy,Izz], inertia_frame [x,y,z,w] (optional: the principal
//             axes in the body frame), position, rotation [x,y,z,w], linvel, angvel,
//             box {half_extents, friction} | null}]
//   forces: [{body, force [x,y,z], point [x,y,z] (world), from_step, to_step}]
//   force_series: {body_id: [[[fx,fy,fz],[px,py,pz]], ...] one entry per step}
//   torque_series: {body_id: [[tx,ty,tz], ...] one world torque per step}
// Forces and torques are cleared and re-applied before every step.

import RAPIER from "@dimforge/rapier3d-compat";

const vec = (a) => ({ x: a[0], y: a[1], z: a[2] });
const arr = (v) => [v.x, v.y, v.z];
const quat = (q) => ({ x: q[0], y: q[1], z: q[2], w: q[3] });

async function readStdin() {
  const chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function run(sc) {
  const world = new RAPIER.World(vec(sc.gravity));
  world.timestep = sc.dt;
  if (sc.solver_iterations) world.integrationParameters.numSolverIterations = sc.solver_iterations;

  for (const b of sc.boxes ?? []) {
    const body = world.createRigidBody(RAPIER.RigidBodyDesc.fixed().setTranslation(...b.center));
    const col = RAPIER.ColliderDesc.cuboid(...b.half_extents).setFriction(b.friction ?? 0.8);
    world.createCollider(col, body);
  }

  const bodies = {};
  for (const b of sc.bodies) {
    const r = b.rotation ?? [0, 0, 0, 1];
    const desc = RAPIER.RigidBodyDesc.dynamic()
      .setTranslation(...b.position)
      .setRotation({ x: r[0], y: r[1], z: r[2], w: r[3] })
      .setLinvel(...(b.linvel ?? [0, 0, 0]))
      .setAngvel(vec(b.angvel ?? [0, 0, 0]))
      .setCanSleep(false)
      .setAdditionalMassProperties(b.mass, vec([0, 0, 0]), vec(b.inertia), quat(b.inertia_frame ?? [0, 0, 0, 1]));
    const body = world.createRigidBody(desc);
    if (b.box) {
      // density 0: mass and inertia come only from the values above
      const col = RAPIER.ColliderDesc.cuboid(...b.box.half_extents).setDensity(0).setFriction(b.box.friction ?? 0.8);
      world.createCollider(col, body);
    }
    bodies[b.id] = body;
  }

  const every = sc.record_every ?? 1;
  const out = { t: [], bodies: {} };
  for (const id of Object.keys(bodies)) out.bodies[id] = { position: [], rotation: [], linvel: [], angvel: [] };
  const record = (step) => {
    out.t.push(step * sc.dt);
    for (const [id, body] of Object.entries(bodies)) {
      const q = body.rotation();
      out.bodies[id].position.push(arr(body.translation()));
      out.bodies[id].rotation.push([q.x, q.y, q.z, q.w]);
      out.bodies[id].linvel.push(arr(body.linvel()));
      out.bodies[id].angvel.push(arr(body.angvel()));
    }
  };

  record(0);
  for (let step = 0; step < sc.steps; step++) {
    for (const body of Object.values(bodies)) {
      body.resetForces(true);
      body.resetTorques(true);
    }
    for (const f of sc.forces ?? []) {
      if (step >= (f.from_step ?? 0) && step < (f.to_step ?? sc.steps)) {
        bodies[f.body].addForceAtPoint(vec(f.force), vec(f.point), true);
      }
    }
    for (const [id, series] of Object.entries(sc.force_series ?? {})) {
      for (const [force, point] of series[step] ?? []) bodies[id].addForceAtPoint(vec(force), vec(point), true);
    }
    for (const [id, series] of Object.entries(sc.torque_series ?? {})) {
      if (series[step]) bodies[id].addTorque(vec(series[step]), true);
    }
    world.step();
    if ((step + 1) % every === 0) record(step + 1);
  }
  out.rapier_version = typeof RAPIER.version === "function" ? RAPIER.version() : null;
  out.solver_iterations = world.integrationParameters.numSolverIterations;
  return out;
}

await RAPIER.init();
const scenario = await readStdin();
process.stdout.write(JSON.stringify(run(scenario)));
