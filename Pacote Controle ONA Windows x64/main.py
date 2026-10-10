"""Unimed Vitória - Gestão de Treinamentos.

Execução:  uvicorn main:app --reload
"""
import json
import os
import re
import shutil
import sqlite3
import sys
import unicodedata
from io import BytesIO
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from openpyxl import load_workbook
from pydantic import BaseModel

# Recursos (static, seed) ficam dentro do programa; o banco fica em Documentos para facilitar o backup.
BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
if getattr(sys, "frozen", False):
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Controle Treinamentos SOS"
    old_db = Path.home() / "Documents" / "Controle Treinamentos SOS" / "sos.db"
    new_db = DATA_DIR / "sos.db"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if old_db.is_file() and not new_db.exists():
        shutil.copy2(old_db, new_db)
else:
    DATA_DIR = BASE
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "sos.db"
SEED = json.loads((BASE / "seed.json").read_text("utf-8"))
PRAZO_FINAL = SEED["config"]["deadline"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS participantes(
  mat TEXT PRIMARY KEY, nome TEXT NOT NULL, cargo TEXT NOT NULL,
  ativo INTEGER NOT NULL DEFAULT 1, email TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS cargos(
  cargo TEXT PRIMARY KEY COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS configuracoes(
  chave TEXT PRIMARY KEY, valor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS turmas(
  id INTEGER PRIMARY KEY, code TEXT, tema TEXT, data_txt TEXT, data_iso TEXT,
  instrutores TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS presencas(
  turma_id INTEGER, mat TEXT, presente INTEGER NOT NULL, justificativa TEXT DEFAULT '',
  PRIMARY KEY(turma_id, mat));
"""


def data_iso(txt: str) -> str:
    """'14-15/02/2026' -> '2026-02-14' (primeiro dia da turma)."""
    m = re.search(r"(\d{1,2})(?:-\d{1,2})?/(\d{2})/(\d{4})", txt)
    return f"{m[3]}-{m[2]}-{int(m[1]):02d}" if m else ""


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def q(con, sql, *args):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def _texto_planilha(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return str(valor)
    if isinstance(valor, int):
        return str(int(valor))
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor).strip()


def _cabecalho_planilha(valor) -> str:
    texto = unicodedata.normalize("NFKD", _texto_planilha(valor).casefold())
    return "".join(c for c in texto if not unicodedata.combining(c) and c.isalnum())


def init_db():
    with db() as c:
        c.executescript(SCHEMA)
        seed_concluido = c.execute(
            "SELECT valor FROM configuracoes WHERE chave = 'seed_concluido'"
        ).fetchone()
        if not seed_concluido:
            if not c.execute("SELECT COUNT(*) FROM participantes").fetchone()[0]:
                for p in SEED["participantes"]:
                    c.execute("INSERT INTO participantes(mat,nome,cargo,ativo) VALUES(?,?,?,?)",
                              (p["mat"], p["nome"], p["cargo"], 0 if p.get("isEx") else 1))
                for t in SEED["turmas"]:
                    c.execute("INSERT INTO turmas VALUES(?,?,?,?,?,?,?)",
                              (t["id"], t["code"], t["tema"], t["data"], data_iso(t["data"]),
                               t["instrutores"], t["status"]))
                    for mat in t.get("presentes", []):
                        c.execute("INSERT OR IGNORE INTO presencas VALUES(?,?,1,'')", (t["id"], mat))
                    for mat, txt in (t.get("justificativas") or {}).items():
                        c.execute("INSERT OR IGNORE INTO presencas VALUES(?,?,0,?)", (t["id"], mat, txt))
            c.execute("INSERT INTO configuracoes(chave,valor) VALUES('seed_concluido','1')")
        cargos_existentes = c.execute(
            "SELECT DISTINCT TRIM(cargo) FROM participantes WHERE TRIM(cargo) != ''"
        ).fetchall()
        c.executemany(
            "INSERT OR IGNORE INTO cargos(cargo) VALUES(?)",
            [(row[0].upper(),) for row in cargos_existentes],
        )


def setor_atual(c) -> str:
    row = c.execute(
        "SELECT valor FROM configuracoes WHERE chave = 'setor'"
    ).fetchone()
    return row["valor"] if row else "Unimed Vitória"


def ativos_com_presencas(c):
    return q(c, """SELECT p.*, (SELECT COUNT(*) FROM presencas s
                   WHERE s.mat = p.mat AND s.presente = 1) AS presencas
                   FROM participantes p WHERE p.ativo = 1 ORDER BY p.nome""")


app = FastAPI(title="Unimed Vitória - Gestão de Treinamentos")
init_db()


def _estado_snapshot(c):
    return {
        "participantes": q(c, "SELECT * FROM participantes ORDER BY ativo DESC, nome"),
        "turmas": q(c, "SELECT * FROM turmas ORDER BY data_iso, id"),
        "vazio": False,
        "setor": setor_atual(c),
    }


@app.get("/api/unidades")
def unidades():
    with db() as c:
        return [{"id": "default", "nome": setor_atual(c), "dados": _estado_snapshot(c)}]


@app.patch("/api/unidades")
def ativa_unidade(item: dict):
    unit_id = str(item.get("id", "")).strip() or "default"
    with db() as c:
        return {"id": unit_id, "nome": setor_atual(c)}


@app.post("/api/unidades")
def cria_unidade(item: dict):
    nome = str(item.get("nome", "")).strip() or "Nova unidade"
    clonar = bool(item.get("clonar", True))
    with db() as c:
        dados = _estado_snapshot(c) if clonar else {"participantes": [], "turmas": [], "vazio": True, "setor": nome}
        return {"id": "default", "nome": nome, "dados": dados}


# ---------- Dashboard (tudo calculado, nada fixo) ----------
@app.get("/api/dashboard")
def dashboard():
    with db() as c:
        ativos = ativos_com_presencas(c)
        pend = [p for p in ativos if p["presencas"] == 0]
        turmas = q(c, "SELECT * FROM turmas ORDER BY data_iso")
        ex = c.execute("SELECT COUNT(*) FROM participantes WHERE ativo = 0").fetchone()[0]
        setor = setor_atual(c)
        programa_apagado = c.execute(
            "SELECT valor FROM configuracoes WHERE chave = 'programa_apagado'"
        ).fetchone()
    status = {t["code"]: t["status"] for t in turmas}
    cargos: dict = {}
    for p in ativos:
        cargos[p["cargo"]] = cargos.get(p["cargo"], 0) + 1

    def feita(entrega):
        return all(status.get(x) == "Realizada" for x in entrega["turmaCode"].split("/"))

    meses: dict = {}
    if not programa_apagado:
        for e in SEED["programa"]:
            m = meses.setdefault(e["periodo"], [0, 0])
            m[0 if feita(e) else 1] += 1
    hoje = date.today().isoformat()
    futuras = [t for t in turmas if t["status"] == "Agendada"]
    proxima = next((t for t in futuras if t["data_iso"] >= hoje), futuras[0] if futuras else None)
    entregas_ok = sum(v[0] for v in meses.values())
    return {
        "kpis": {"base": len(ativos), "treinados": len(ativos) - len(pend),
                 "pendentes": len(pend), "ex": ex, "prazo": PRAZO_FINAL},
        "pendentes": pend, "cargos": cargos, "proxima": proxima, "setor": setor,
        "programa": {"meses": meses, "entregas_ok": entregas_ok,
                     "entregas": sum(sum(v) for v in meses.values()),
                     "turmas_ok": sum(t["status"] == "Realizada" for t in turmas),
                     "turmas": len(turmas)},
    }


# ---------- Participantes ----------
class NovoParticipante(BaseModel):
    nome: str
    mat: str
    cargo: str


class NovoCargo(BaseModel):
    cargo: str


class LimpaDados(BaseModel):
    setor: str = ""
    confirmar: bool


class EditaParticipante(BaseModel):
    email: Optional[str] = None
    ativo: Optional[bool] = None


class NovaTurma(BaseModel):
    tema: str
    data: date
    instrutores: str = ""


@app.get("/api/participantes")
def participantes():
    with db() as c:
        return q(c, """SELECT p.*, (SELECT COUNT(*) FROM presencas s
                       WHERE s.mat = p.mat AND s.presente = 1) AS presencas
                       FROM participantes p ORDER BY p.ativo DESC, p.nome""")


@app.get("/api/cargos")
def listar_cargos():
    with db() as c:
        return [row["cargo"] for row in q(c, "SELECT cargo FROM cargos ORDER BY cargo COLLATE NOCASE")]


@app.post("/api/cargos", status_code=201)
def cria_cargo(body: NovoCargo):
    cargo = body.cargo.strip().upper()
    if not cargo:
        raise HTTPException(422, "Informe o nome do cargo.")
    if len(cargo) > 80:
        raise HTTPException(422, "O nome do cargo deve ter no máximo 80 caracteres.")
    with db() as c:
        cursor = c.execute("INSERT OR IGNORE INTO cargos(cargo) VALUES(?)", (cargo,))
        if not cursor.rowcount:
            raise HTTPException(409, "Este cargo já está cadastrado.")
    return {"cargo": cargo}


@app.delete("/api/cargos")
def remove_cargo(body: NovoCargo):
    cargo = body.cargo.strip()
    if not cargo:
        raise HTTPException(422, "Selecione um cargo para remover.")
    with db() as c:
        row = c.execute(
            "SELECT cargo FROM cargos WHERE cargo = ? COLLATE NOCASE",
            (cargo,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Cargo não encontrado.")
        quantidade = c.execute(
            "SELECT COUNT(*) FROM participantes WHERE UPPER(TRIM(cargo)) = UPPER(?)",
            (row["cargo"],),
        ).fetchone()[0]
        if quantidade:
            raise HTTPException(
                409,
                f"Não é possível remover este cargo: {quantidade} participante(s) ainda utiliza(m) esse cadastro.",
            )
        c.execute("DELETE FROM cargos WHERE cargo = ? COLLATE NOCASE", (row["cargo"],))
    return {"cargo": row["cargo"], "removido": True}


@app.post("/api/limpar")
def limpar_dados(body: LimpaDados):
    if not body.confirmar:
        raise HTTPException(422, "Confirme a exclusão antes de apagar os dados.")
    setor = body.setor.strip()[:120] or "Unimed Vitória"
    with db() as c:
        c.execute("DELETE FROM presencas")
        c.execute("DELETE FROM turmas")
        c.execute("DELETE FROM participantes")
        c.execute("DELETE FROM cargos")
        c.execute(
            """INSERT INTO configuracoes(chave,valor) VALUES('setor',?)
               ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor""",
            (setor,),
        )
        c.execute(
            """INSERT INTO configuracoes(chave,valor) VALUES('seed_concluido','1')
               ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor"""
        )
        c.execute(
            """INSERT INTO configuracoes(chave,valor) VALUES('programa_apagado','1')
               ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor"""
        )
    return {"ok": True, "setor": setor}


@app.post("/api/participantes", status_code=201)
def cria_participante(p: NovoParticipante):
    with db() as c:
        if c.execute("SELECT 1 FROM participantes WHERE mat = ?", (p.mat,)).fetchone():
            raise HTTPException(409, "Matrícula já cadastrada.")
        c.execute("INSERT INTO participantes(mat,nome,cargo) VALUES(?,?,?)",
                  (p.mat.strip(), p.nome.strip(), p.cargo))
        c.execute("INSERT OR IGNORE INTO cargos(cargo) VALUES(?)", (p.cargo.strip(),))
    return {"ok": True}


@app.post("/api/participantes/importar")
async def importar_participantes(request: Request, filename: str = ""):
    if Path(filename).suffix.casefold() != ".xlsx":
        raise HTTPException(400, "Selecione uma planilha Excel no formato .xlsx.")
    conteudo = await request.body()
    if not conteudo:
        raise HTTPException(400, "O arquivo selecionado está vazio.")
    if len(conteudo) > 10 * 1024 * 1024:
        raise HTTPException(413, "O arquivo deve ter no máximo 10 MB.")

    try:
        workbook = load_workbook(BytesIO(conteudo), read_only=True, data_only=True)
    except Exception as exc:
        raise HTTPException(400, "Não foi possível ler a planilha. Verifique se o arquivo .xlsx é válido.") from exc

    try:
        sheet = workbook.active
        rows = sheet.iter_rows(values_only=True)
        headers = next(rows, None)
        if not headers:
            raise HTTPException(422, "A planilha está vazia. A primeira linha deve conter os cabeçalhos.")

        aliases = {
            "mat": {"mat", "matricula"},
            "nome": {"nome", "nomecompleto"},
            "cargo": {"cargo", "funcao"},
            "email": {"email", "enderecodeemail"},
        }
        columns = {}
        for index, value in enumerate(headers):
            normalized = _cabecalho_planilha(value)
            for field, names in aliases.items():
                if normalized in names:
                    columns.setdefault(field, index)
        missing = [field for field in ("mat", "nome", "cargo") if field not in columns]
        if missing:
            labels = {"mat": "Matrícula", "nome": "Nome", "cargo": "Cargo"}
            raise HTTPException(
                422,
                "Colunas obrigatórias não encontradas: " + ", ".join(labels[field] for field in missing) + ".",
            )

        novos = []
        erros = []
        for numero, row in enumerate(rows, start=2):
            if numero > 10001:
                raise HTTPException(422, "A planilha pode conter no máximo 10.000 linhas de dados.")
            if not any(_texto_planilha(value) for value in row):
                continue

            def valor(field):
                index = columns.get(field)
                return _texto_planilha(row[index] if index is not None and index < len(row) else None)

            mat, nome, cargo, email = (valor(field) for field in ("mat", "nome", "cargo", "email"))
            faltando = [label for field, label in (("mat", "Matrícula"), ("nome", "Nome"), ("cargo", "Cargo"))
                        if not valor(field)]
            if faltando:
                erros.append(f"Linha {numero}: campo obrigatório vazio ({', '.join(faltando)}).")
                continue
            if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                erros.append(f"Linha {numero}: e-mail inválido.")
                continue
            novos.append((mat, nome, cargo, email, numero))
    finally:
        workbook.close()

    inseridos = 0
    duplicados = 0
    with db() as c:
        existentes = {row["mat"] for row in c.execute("SELECT mat FROM participantes")}
        for mat, nome, cargo, email, numero in novos:
            if mat in existentes:
                duplicados += 1
                continue
            c.execute(
                "INSERT INTO participantes(mat,nome,cargo,email) VALUES(?,?,?,?)",
                (mat, nome, cargo, email),
            )
            c.execute("INSERT OR IGNORE INTO cargos(cargo) VALUES(?)", (cargo,))
            existentes.add(mat)
            inseridos += 1

    return {"inseridos": inseridos, "duplicados": duplicados, "erros": erros}


@app.patch("/api/participantes/{mat}")
def edita_participante(mat: str, d: EditaParticipante):
    with db() as c:
        if d.email is not None:
            email = d.email.strip()
            if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                raise HTTPException(422, "E-mail inválido.")
            c.execute("UPDATE participantes SET email = ? WHERE mat = ?", (email, mat))
        if d.ativo is not None:
            c.execute("UPDATE participantes SET ativo = ? WHERE mat = ?", (int(d.ativo), mat))
    return {"ok": True}


# ---------- Turmas e presença ----------
class Registro(BaseModel):
    mat: str
    presente: bool
    justificativa: str = ""


class Presenca(BaseModel):
    registros: list[Registro]


@app.get("/api/turmas")
def turmas():
    with db() as c:
        return q(c, """SELECT t.*, (SELECT COUNT(*) FROM presencas s
                       WHERE s.turma_id = t.id AND s.presente = 1) AS presentes
                       FROM turmas t ORDER BY t.data_iso, t.id""")


@app.post("/api/turmas", status_code=201)
def cria_turma(body: NovaTurma):
    tema = body.tema.strip()
    if not tema:
        raise HTTPException(422, "Informe o tema do treinamento.")
    data_txt = body.data.strftime("%d/%m/%Y")
    with db() as c:
        codes = q(c, "SELECT code FROM turmas")
        numero = max(
            (int(match[1]) for row in codes if (match := re.fullmatch(r"T(\d+)", row["code"] or ""))),
            default=0,
        ) + 1
        code = f"T{numero}"
        c.execute(
            """INSERT INTO turmas(code,tema,data_txt,data_iso,instrutores,status)
               VALUES(?,?,?,?,?,'Agendada')""",
            (code, tema, data_txt, body.data.isoformat(), body.instrutores.strip()),
        )
        turma_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"id": turma_id, "code": code}


@app.delete("/api/turmas/{tid}")
def exclui_turma(tid: int):
    with db() as c:
        if not c.execute("SELECT 1 FROM turmas WHERE id = ?", (tid,)).fetchone():
            raise HTTPException(404, "Turma não encontrada.")
        c.execute("DELETE FROM presencas WHERE turma_id = ?", (tid,))
        c.execute("DELETE FROM turmas WHERE id = ?", (tid,))
    return {"ok": True, "id": tid}


@app.get("/api/turmas/{tid}")
def turma(tid: int):
    with db() as c:
        t = q(c, "SELECT * FROM turmas WHERE id = ?", tid)
        if not t:
            raise HTTPException(404, "Turma não encontrada.")
        reg = {r["mat"]: r for r in q(c, "SELECT * FROM presencas WHERE turma_id = ?", tid)}
        lista = [{"mat": p["mat"], "nome": p["nome"], "cargo": p["cargo"],
                  "presente": (bool(reg[p["mat"]]["presente"]) if p["mat"] in reg else None),
                  "justificativa": reg.get(p["mat"], {}).get("justificativa", "")}
                 for p in ativos_com_presencas(c)]
    return {"turma": t[0], "participantes": lista}


@app.put("/api/turmas/{tid}/presenca")
def salva_presenca(tid: int, body: Presenca):
    for r in body.registros:
        if not r.presente and not r.justificativa.strip():
            raise HTTPException(422, f"Justificativa obrigatória para a ausência da matrícula {r.mat}.")
    with db() as c:
        c.execute("DELETE FROM presencas WHERE turma_id = ?", (tid,))
        c.executemany("INSERT INTO presencas VALUES(?,?,?,?)",
                      [(tid, r.mat, int(r.presente), r.justificativa.strip()) for r in body.registros])
        if body.registros:
            c.execute("UPDATE turmas SET status = 'Realizada' WHERE id = ?", (tid,))
    return {"ok": True}


# ---------- Cobertura por tema (temas cadastrados nas turmas) ----------
@app.get("/api/cobertura")
def cobertura():
    with db() as c:
        participantes_ativos = ativos_com_presencas(c)
        ativos = {p["mat"] for p in participantes_ativos}
        temas = q(c, """SELECT TRIM(tema) AS tema FROM turmas
                        WHERE TRIM(COALESCE(tema, '')) != ''
                        GROUP BY LOWER(TRIM(tema)) ORDER BY tema COLLATE NOCASE""")
        linhas = q(c, """SELECT LOWER(TRIM(t.tema)) AS tema_chave, s.mat, t.data_iso FROM presencas s
                         JOIN turmas t ON t.id = s.turma_id
                         WHERE s.presente = 1 ORDER BY t.data_iso""")
        datas_inicio = q(c, """SELECT LOWER(TRIM(tema)) AS tema_chave, MIN(NULLIF(data_iso, '')) AS inicio
                               FROM turmas WHERE status = 'Realizada'
                               GROUP BY LOWER(TRIM(tema))""")
    hoje = date.today()
    inicio_por_tema = {r["tema_chave"]: r["inicio"] for r in datas_inicio}
    por_tema: dict = {}
    primeira_presenca: dict = {}
    for r in linhas:
        tema = r["tema_chave"]
        por_tema.setdefault(tema, set()).add(r["mat"])
        por_tema[tema] &= ativos
        if r["mat"] in ativos:
            primeira_presenca.setdefault(tema, {}).setdefault(r["mat"], r["data_iso"])
    out = []
    for item in temas:
        tema = item["tema"]
        tema_chave = tema.casefold()
        ok = len(por_tema.get(tema_chave, set()) & ativos)
        inicio = inicio_por_tema.get(tema_chave)
        prazo = date.fromisoformat(inicio) + timedelta(days=60) if inicio else None
        concluido = bool(ativos) and ok == len(ativos)
        datas_participantes = primeira_presenca.get(tema_chave, {})
        concluido_em = max(datas_participantes.values()) if concluido and datas_participantes else None
        status = ("sem_participantes" if not ativos else
                  "concluido" if concluido else
                  "nao_iniciado" if prazo is None else
                  "atrasado" if prazo < hoje else "em_andamento")
        out.append({
            "tema": tema,
            "treinados": ok,
            "pendentes": len(ativos) - ok,
            "pct": round(100 * ok / len(ativos)) if ativos else 0,
            "inicio": inicio,
            "prazo": prazo.isoformat() if prazo else None,
            "dias_restantes": (prazo - hoje).days if prazo and not concluido else None,
            "concluido_em": concluido_em,
            "concluido_no_prazo": concluido_em <= prazo.isoformat() if concluido_em and prazo else None,
            "status": status,
        })
    return sorted(out, key=lambda x: (-x["pct"], x["tema"].casefold()))


@app.get("/api/relatorio")
def relatorio():
    with db() as c:
        ativos = ativos_com_presencas(c)
        pres = q(c, """SELECT t.code, s.mat FROM presencas s JOIN turmas t ON t.id = s.turma_id
                       WHERE s.presente = 1 ORDER BY t.data_iso, t.id""")
    de: dict = {}
    for r in pres:
        de.setdefault(r["mat"], []).append(r["code"])
    linhas = [{**p, "turmas": de.get(p["mat"], [])} for p in sorted(ativos, key=lambda p: (p["cargo"], p["nome"]))]
    return {"gerado": datetime.now().strftime("%d/%m/%Y %H:%M"), "prazo": PRAZO_FINAL, "base": len(linhas),
            "treinados": [l for l in linhas if l["presencas"] > 0],
            "nao_treinados": [l for l in linhas if l["presencas"] == 0]}


@app.get("/")
def index():
    html = (BASE / "static" / "index.html").read_text("utf-8")
    if (BASE / "static" / "plotly.min.js").exists():  # uso offline: biblioteca de gráficos local
        html = html.replace("https://cdn.plot.ly/plotly-2.35.2.min.js", "/static/plotly.min.js")
    return HTMLResponse(html)


app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
