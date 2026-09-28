// Dashboard de Sofía. Compila a app/api/static/dashboard.js (npm run build).
// Los tipos reflejan el JSON de GET /dashboard/data (app/api/dashboard.py):
// si el backend cambia la forma, actualizar aquí y tsc señala lo que rompe.

type ErrorBloque = { error: string };

interface Llamadas {
  active: number;
  total: number;
  today: number;
  errors_today: number;
  last_call_at: string | null;
  by_day: { date: string; calls: number }[];
}

interface Citas {
  created_today: Record<string, number>;
  next_24h: number;
  reminders: Record<string, number>;
}

interface Datos {
  status: "ok" | "degraded";
  env: string;
  uptime_s: number;
  server_time: string;
  llm: { provider: string; model: string };
  dependencies: Record<string, string>;
  integrations: Record<string, boolean>;
  calls: Llamadas | ErrorBloque;
  appointments: Citas | ErrorBloque;
}

const REFRESCO_MS = 5000;
const CLAVE_STORAGE = "sofia_clave";

const $ = <T extends HTMLElement = HTMLElement>(id: string): T => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`falta #${id} en dashboard.html`);
  return el as T;
};

const esError = (x: object): x is ErrorBloque => "error" in x;

let clave = "";
try { clave = sessionStorage.getItem(CLAVE_STORAGE) ?? ""; } catch { /* storage bloqueado */ }

function fila(cont: HTMLElement, nombre: string, valor: string, clase = ""): void {
  const d = document.createElement("div");
  const a = document.createElement("span");
  a.textContent = nombre;
  const b = document.createElement("span");
  b.textContent = valor;
  if (clase) b.className = `pill ${clase}`;
  d.append(a, b);
  cont.append(d);
}

function filas(
  id: string,
  obj: Record<string, string | number> | ErrorBloque,
  clasificar?: (v: string) => string,
): void {
  const c = $(id);
  c.replaceChildren();
  if (esError(obj)) { fila(c, "Error", obj.error, "bad"); return; }
  const entradas = Object.entries(obj);
  if (!entradas.length) fila(c, "Sin registros", "0");
  for (const [k, v] of entradas) fila(c, k, String(v), clasificar?.(String(v)));
}

function uptime(s: number): string {
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}

function pintarLlamadas(c: Llamadas | ErrorBloque): void {
  const ok = !esError(c);
  $("activas").textContent = ok ? String(c.active) : "—";
  $("hoy").textContent = ok ? String(c.today) : "—";
  $("total").textContent = ok ? String(c.total) : "—";
  $("errores").textContent = ok ? String(c.errors_today) : "—";
  $("errores").className = "big" + (ok && c.errors_today ? " bad" : "");
  $("ultima").textContent = ok && c.last_call_at ? new Date(c.last_call_at).toLocaleString() : "—";

  const barras = $("barras");
  barras.replaceChildren();
  const dias = ok ? c.by_day : [];
  const max = Math.max(1, ...dias.map((d) => d.calls));
  for (const d of dias) {
    const col = document.createElement("div");
    const n = document.createElement("span");
    n.textContent = String(d.calls);
    const b = document.createElement("span");
    b.className = "b";
    b.style.height = `${(d.calls / max) * 100}%`;
    b.title = `${d.date}: ${d.calls} llamadas`;
    const f = document.createElement("span");
    f.className = "muted";
    f.textContent = d.date.slice(5);
    col.append(n, b, f);
    barras.append(col);
  }
}

function pintar(x: Datos): void {
  const ok = x.status === "ok";
  $("estado").className = `pill ${ok ? "ok" : "bad"}`;
  $("estado").textContent = `${ok ? "Operativo" : "Degradado"} · ${x.env}`;
  $("uptime").textContent = uptime(x.uptime_s);

  pintarLlamadas(x.calls);

  filas("deps", x.dependencies, (v) => (v === "ok" ? "ok" : v === "desactivado" ? "muted" : "bad"));
  filas(
    "integ",
    Object.fromEntries(Object.entries(x.integrations).map(([k, v]) => [k, v ? "configurada" : "falta"])),
    (v) => (v === "configurada" ? "ok" : "warn"),
  );
  fila($("integ"), "modelo", `${x.llm.provider} / ${x.llm.model}`);

  const a = x.appointments;
  if (esError(a)) {
    filas("citas", a);
    filas("rec", a);
  } else {
    filas("citas", {
      "próximas 24 h": a.next_24h,
      ...Object.fromEntries(Object.entries(a.created_today).map(([k, v]) => [`creadas hoy · ${k}`, v])),
    });
    filas("rec", a.reminders);
  }
  $("actualizado").textContent = `Actualizado ${new Date().toLocaleTimeString()}`;
}

async function refrescar(): Promise<void> {
  const err = $("err");
  if (!clave) { err.textContent = "Introduce la clave interna"; return; }
  try {
    const r = await fetch("/dashboard/data", { headers: { "X-Internal-Key": clave } });
    if (r.status === 401) { err.textContent = "Clave inválida"; return; }
    if (!r.ok) { err.textContent = `HTTP ${r.status}`; return; }
    pintar((await r.json()) as Datos);
    err.textContent = "";
  } catch {
    err.textContent = "API inaccesible";
    $("estado").className = "pill bad";
    $("estado").textContent = "Sin conexión";
  }
}

$<HTMLFormElement>("f").addEventListener("submit", (e) => {
  e.preventDefault();
  clave = $<HTMLInputElement>("clave").value.trim();
  try { sessionStorage.setItem(CLAVE_STORAGE, clave); } catch { /* storage bloqueado */ }
  void refrescar();
});

void refrescar();
setInterval(() => void refrescar(), REFRESCO_MS);
