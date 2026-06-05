import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
from datetime import datetime, timedelta
import asyncio
import json
import zoneinfo
import gspread
import json
from tabulate import tabulate

# --- MÓDULOS PARA A GAMBIARRA DO RENDER (FLASK) ---
from flask import Flask
from threading import Thread
import os

app = Flask('')

@app.route('/')
def home():
    return "⚔️ O Bot da SuicideBoys está ONLINE com Categorias Dinâmicas!"

def run_web_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server)
    t.start()
# --------------------------------------------------
# --- CONEXÃO COM O GOOGLE SHEETS ---
try:
    google_creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if google_creds_json:
        creds_dict = json.loads(google_creds_json)
        gc = gspread.service_account_from_dict(creds_dict)
        
        # Troque para o nome exato da sua planilha
        planilha = gc.open("DB-Teste-G59") 
        aba_principal = planilha.sheet1
        
        aba_principal.update_acell('A1', 'O Bot G59 conseguiu conectar no Google Sheets!')
        print("✅ Conexão com Google Sheets estabelecida com sucesso!")
    else:
        print("⚠️ Credenciais não encontradas no Render.")
except Exception as e:
    print(f"❌ Erro ao conectar: {e}")
# -----------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

BR_TIMEZONE = zoneinfo.ZoneInfo("America/Sao_Paulo")

# --- 👑 LISTA DE CATEGORIAS PADRÕES ATUALIZADA E NA ORDEM EXATA ---
CATEGORIAS_PADRAO_INICIAIS = [
"👑 CALLER", "👊 STRIKER", "💥 ZERK SUCC", "🏹 ARCHER/RANGER", 
"🎸 SHAI", "🛡️ NOVA SUCC", "㊙️ DO-SA", "🪶 SUPORTE", 
"🥷 SCOUT", "⚔️ ATAQUE", "🏳️ BANDEIRA", "🐘 ELEFANTE", 
"🏛️ DEFESA"
]

presencas_ativas = {}
wait_list_geral = []

DIAS_DA_SEMANA_PT = {
0: "Segunda-feira", 1: "Terça-feira", 2: "Quarta-feira", 
3: "Quinta-feira", 4: "Sexta-feira", 5: "Sábado", 6: "Domingo"
}

# --- 🧠 MEMÓRIA CACHE DO BOT (Evita bloqueio do Google) ---
CACHE_CONFIG = {}
CACHE_CRONOGRAMA = {}
CACHE_PRESETS = {}

async def sincronizar_planilha():
    global CACHE_CONFIG, CACHE_CRONOGRAMA, CACHE_PRESETS
    try:
        # 1. Conecta no Google
        google_creds_json = os.environ.get("GOOGLE_CREDENTIALS")
        creds_dict = json.loads(google_creds_json)
        gc = gspread.service_account_from_dict(creds_dict)
        planilha = gc.open("DB-Teste-G59") # Troque se o nome for diferente
        
        # 2. Puxa as Configurações Gerais
        aba_config = planilha.worksheet("Config_Geral")
        dados_config = aba_config.get_all_values()
        CACHE_CONFIG.clear()
        for linha in dados_config[1:]: # Pula a linha 1 (Cabeçalho)
            if len(linha) >= 2 and linha[0].strip() != "":
                CACHE_CONFIG[linha[0].strip()] = linha[1].strip()

        # 3. Puxa o Cronograma Semanal
        aba_crono = planilha.worksheet("Cronograma")
        dados_crono = aba_crono.get_all_values()
        CACHE_CRONOGRAMA.clear()
        for linha in dados_crono[1:]:
            if len(linha) >= 2 and linha[0].strip() != "":
                CACHE_CRONOGRAMA[linha[0].strip()] = linha[1].strip()

        # 4. Puxa os Presets de Guerra
        aba_presets = planilha.worksheet("Setup_Presets")
        dados_presets = aba_presets.get_all_values()
        CACHE_PRESETS.clear()
        for linha in dados_presets[1:]:
            if len(linha) >= 3 and linha[0].strip() != "":
                nome_preset = linha[0].strip()
                classe = linha[1].strip()
                limite = linha[2].strip()
                travas = linha[3].strip() if len(linha) > 3 else ""
                
                if nome_preset not in CACHE_PRESETS:
                    CACHE_PRESETS[nome_preset] = []
                CACHE_PRESETS[nome_preset].append({"classe": classe, "limite": limite, "travas": travas})
        
        return True, "✅ Sincronização concluída! O bot aprendeu as configurações da planilha."
    except Exception as e:
        print(f"❌ Erro na Sincronização: {e}")
        return False, f"❌ Erro ao ler a planilha. Verifique se os nomes das abas estão exatos: {e}"
# -----------------------------------------------------------

init_db(reset=False)

# --- FUNÇÕES DE SUPORTE AO BANCO DE DADOS DINÂMICO ---
def carregar_categorias_db(guild_id):
    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()
    cursor.execute("SELECT classe, limite FROM categorias_sistema WHERE guild_id = ? ORDER BY rowid ASC", (str(guild_id),))
    linhas = cursor.fetchall()

    if not linhas:
        for cat in CATEGORIAS_PADRAO_INICIAIS:
            cursor.execute("INSERT OR IGNORE INTO categorias_sistema (guild_id, classe, limite) VALUES (?, ?, 0)", (str(guild_id), cat))
        conn.commit()
        cursor.execute("SELECT classe, limite FROM categorias_sistema WHERE guild_id = ? ORDER BY rowid ASC", (str(guild_id),))
        linhas = cursor.fetchall()

    conn.close()
    return {classe: limite for classe, limite in linhas}

def carregar_requisitos_cargos(guild_id):
    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()
    cursor.execute("SELECT classe, cargo_id FROM requisitos_classes WHERE guild_id = ?", (str(guild_id),))
    linhas = cursor.fetchall()
    conn.close()
    return {classe: cargo_id for classe, cargo_id in linhas}

def verificar_status_automacao(guild_id):
    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()
    cursor.execute("SELECT automacao_ativa FROM status_global WHERE guild_id = ?", (str(guild_id),))
    res = cursor.fetchone()
    conn.close()
    return res[0] if res else 1

def configurar_dias_padrao(guild_id):
    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM cronograma_semanal WHERE guild_id = ?", (str(guild_id),))
    if cursor.fetchone()[0] == 0:
        valores_padrao = [
            (str(guild_id), 0, "t2-40", 1),
            (str(guild_id), 1, "t2-40", 1),
            (str(guild_id), 2, "t2-40", 1),
            (str(guild_id), 3, "t2-40", 1),
            (str(guild_id), 4, "t1-25", 1),
            (str(guild_id), 5, "t2-40", 0),
            (str(guild_id), 6, "t1-30", 1)
        ]
        cursor.executemany(
            "INSERT INTO cronograma_semanal (guild_id, dia_guerra, nome_preset, ativo) VALUES (?, ?, ?, ?)",
            valores_padrao
        )
        conn.commit()
    conn.close()

def gerar_texto_painel(guild_id, guild_obj=None):
    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()
    
    # 1. Lógica da hora corte (Data Inteligente)
    cursor.execute("SELECT hora_criar FROM config WHERE guild_id = ?", (str(guild_id),))
    res_config = cursor.fetchone()
    
    hora_corte, minuto_corte = 22, 10
    if res_config and res_config[0]:
        try:
            partes = str(res_config[0]).split(":")
            hora_corte = int(partes[0])
            minuto_corte = int(partes[1])
        except:
            pass

    agora = datetime.now(BR_TIMEZONE)
    
    if agora.hour < hora_corte or (agora.hour == hora_corte and agora.minute < minuto_corte):
        data_alvo = agora
    else:
        data_alvo = agora + timedelta(days=1)
        
    dia_nome = DIAS_DA_SEMANA_PT[data_alvo.weekday()]
    data_formatada = data_alvo.strftime("%d/%m")

    # 2. Puxa os limites PRIMEIRO para o bot descobrir qual é o Tier da Guerra
    cursor.execute("SELECT classe, limite FROM categorias_sistema WHERE guild_id = ? ORDER BY rowid ASC", (str(guild_id),))
    limites = {str(row[0]).strip(): int(row[1]) for row in cursor.fetchall()}
    
    # Truque matemático: soma as vagas para descobrir o Preset
    total_vagas = sum(limites.values())
    if total_vagas >= 40:
        titulo_evento = "NODE WAR T2"
    elif total_vagas > 0 and total_vagas <= 35:
        titulo_evento = "NODE WAR T1"
    else:
        titulo_evento = "NODE WAR" # Prevenção padrão caso criem um preset diferente

    # 3. Cria o embed já com o título inteligente
    embed = discord.Embed(
        title=f"📅 {titulo_evento} - {dia_nome} {data_formatada}",
        description="Verifique os requisitos de vagas e clique no botão correspondente para se inscrever.",
        color=discord.Color.from_rgb(47, 49, 54)
    )

    cursor.execute("SELECT classe, cargo_id FROM requisitos_classes")
    cargos_req_clean = {str(linha[0]).strip().lower(): str(linha[1]).strip() for linha in cursor.fetchall()}
    conn.close()

    categorias_visiveis = 0
    for cat, max_vagas in limites.items():
        if max_vagas <= 0: continue

        categorias_visiveis += 1
        inscritos = presencas_ativas.get(cat, [])
        vagas_texto = ", ".join([f"<@{uid}>" for uid in inscritos]) if inscritos else "-"

        req_cargo = "Liberado"
        cat_lower = cat.lower().strip()
        
        # Match Dinâmico de Tags
        for classe_db, cargo_id in cargos_req_clean.items():
            if classe_db in cat_lower or cat_lower in classe_db:
                req_cargo = f"<@&{cargo_id}>"
                break

        embed.add_field(name=f"{cat} ({len(inscritos)}/{max_vagas})", value=f"🔒 **{req_cargo}**\n{vagas_texto}", inline=True)

    if categorias_visiveis == 0:
        embed.description = "⚠️ Nenhuma categoria ativa com vagas abertas para esta Guerra."

    embed.add_field(name="​", value="⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯", inline=False)
    texto_wait = "\n".join([f"⏳ #{i+1} <@{j['user_id']}> ➔ **{j['funcao']}**" for i, j in enumerate(wait_list_geral)]) if wait_list_geral else "*Fila vazia*"
    embed.add_field(name="⏳ Waitlist / Fila", value=texto_wait, inline=False)
    
    return embed

async def notificar_promovido_dm(bot_client, user_id, guild_id, classe_nome):
    try:
        msg_final = ("Você foi promovido da fila de espera e convocado para a GUERRA! Garanta sua participação dentro do jogo!\n"
                     "Qualquer dúvida, entre em contato com algum @Staff.")
        user = await bot_client.fetch_user(user_id)
        await user.send(msg_final)
    except Exception: 
        pass

async def atualizar_painel_existente(guild):
    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()
    cursor.execute("SELECT canal_automacao_id, painel_msg_id FROM config WHERE guild_id = ?", (str(guild.id),))
    res = cursor.fetchone()
    conn.close()
    if res and res[0] and res[1]:
        try:
            canal = guild.get_channel(int(res[0])) or await guild.fetch_channel(int(res[0]))
            msg = await canal.fetch_message(int(res[1]))
            nova_view = GradeBotoesView(guild.id)
            await msg.edit(embed=gerar_texto_painel(guild.id, guild), view=nova_view)
        except Exception: 
            pass

class BotaoClasseLista(discord.ui.Button):
    def __init__(self, label, row, custom_id):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=row, custom_id=custom_id)

    async def callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        cat_nome = self.label

        # Traz a mesma inteligência à prova de falhas do painel visual
        conn = sqlite3.connect("guild_nodewar.db")
        cursor = conn.cursor()
        cursor.execute("SELECT classe, limite FROM categorias_sistema WHERE guild_id = ?", (str(interaction.guild.id),))
        limites = {str(row[0]).strip(): int(row[1]) for row in cursor.fetchall()}
        
        cursor.execute("SELECT classe, cargo_id FROM requisitos_classes")
        cargos_req_clean = {str(linha[0]).strip().lower(): str(linha[1]).strip() for linha in cursor.fetchall()}
        conn.close()

        cat_lower = cat_nome.lower().strip()
        cargo_id_req = None
        
        # Match Dinâmico: Acha a trava certa ignorando emojis
        for classe_db, c_id in cargos_req_clean.items():
            if classe_db in cat_lower or cat_lower in classe_db:
                cargo_id_req = c_id
                break

        limite = limites.get(cat_nome, 0)

        if limite <= 0:
            return await interaction.response.send_message("❌ Esta categoria não possui vagas disponíveis.", ephemeral=True)

        if cargo_id_req:
            cargo_obj = interaction.guild.get_role(int(cargo_id_req))
            
            # Validação implacável: checa todos os cargos dentro do perfil do usuário
            has_role = False
            if cargo_obj and cargo_obj in interaction.user.roles:
                has_role = True
            else:
                has_role = any(str(r.id) == str(cargo_id_req) for r in getattr(interaction.user, 'roles', []))
                
            if not has_role:
                mencao_cargo = cargo_obj.mention if cargo_obj else f"<@&{cargo_id_req}>"
                return await interaction.response.send_message(f"❌ Acesso Negado! Você precisa do cargo {mencao_cargo} para se inscrever nesta classe.", ephemeral=True)

        if cat_nome not in presencas_ativas:
            presencas_ativas[cat_nome] = []

        if user_id in presencas_ativas[cat_nome]:
            return await interaction.response.send_message("⚠️ Você já está cadastrado nesta função.", ephemeral=True)

        global wait_list_geral

        for c in list(presencas_ativas.keys()):
            if user_id in presencas_ativas[c]:
                presencas_ativas[c].remove(user_id)
                for i, j in enumerate(wait_list_geral):
                    if j["funcao"] == c:
                        promovido = wait_list_geral.pop(i)
                        if c not in presencas_ativas: presencas_ativas[c] = []
                        presencas_ativas[c].append(promovido["user_id"])
                        asyncio.create_task(notificar_promovido_dm(interaction.client, promovido["user_id"], interaction.guild.id, c))
                        break

        wait_list_geral = [w for w in wait_list_geral if w["user_id"] != user_id]

        if len(presencas_ativas[cat_nome]) < limite:
            presencas_ativas[cat_nome].append(user_id)
            msg_resposta = f"✅ Você pegou a vaga de **{cat_nome}**!"
        else:
            wait_list_geral.append({"user_id": user_id, "funcao": cat_nome})
            msg_resposta = f"⏳ Vagas cheias! Entrou na fila de espera."

        nova_view = GradeBotoesView(interaction.guild.id)
        await interaction.response.edit_message(embed=gerar_texto_painel(interaction.guild.id, interaction.guild), view=nova_view)
        await interaction.followup.send(msg_resposta, ephemeral=True)

class BotaoSairPainel(discord.ui.Button):
    def __init__(self, row):
        super().__init__(label="❌ Sair", style=discord.ButtonStyle.danger, row=row, custom_id="btn_nodewar_sair")

    async def callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        global wait_list_geral
        removido = False

        for c in list(presencas_ativas.keys()):
            if user_id in presencas_ativas[c]:
                presencas_ativas[c].remove(user_id)
                removido = True
                for i, j in enumerate(wait_list_geral):
                    if j["funcao"] == c:
                        promovido = wait_list_geral.pop(i)
                        if c not in presencas_ativas: presencas_ativas[c] = []
                        presencas_ativas[c].append(promovido["user_id"])
                        asyncio.create_task(notificar_promovido_dm(interaction.client, promovido["user_id"], interaction.guild.id, c))
                        break
                break

        tamanho_antes = len(wait_list_geral)
        wait_list_geral = [w for w in wait_list_geral if w["user_id"] != user_id]
        if len(wait_list_geral) < tamanho_antes: removido = True

        if removido:
            nova_view = GradeBotoesView(interaction.guild.id)
            await interaction.response.edit_message(embed=gerar_texto_painel(interaction.guild.id, interaction.guild), view=nova_view)
            await interaction.followup.send("👋 Você removeu sua inscrição.", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ Você não está inscrito.", ephemeral=True)

class GradeBotoesView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        limites = carregar_categorias_db(guild_id)

        row_tracker = 0
        buttons_in_row = 0

        for cat, limite in limites.items():
            if limite > 0:
                if buttons_in_row >= 4:
                    row_tracker += 1
                    buttons_in_row = 0

                safe_id = cat.lower().replace('/', '_').replace('-', '_').replace(' ', '_')
                self.add_item(BotaoClasseLista(label=cat, row=row_tracker, custom_id=f"btn_nw_dyn_{safe_id}"))
                buttons_in_row += 1

        row_tracker += 1
        self.add_item(BotaoSairPainel(row=row_tracker))

async def ejecutar_criacao_sistema(guild, canal, nome_preset: str):
    global presencas_ativas, wait_list_geral

    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()

    # --- 🛑 HIGHLANDER: Deleta o painel fantasma antigo antes de abrir um novo ---
    cursor.execute("SELECT painel_msg_id, canal_automacao_id, aviso_msg_id FROM config WHERE guild_id = ?", (str(guild.id),))
    res_antigo = cursor.fetchone()
    if res_antigo and res_antigo[0] and res_antigo[1]:
        try:
            canal_antigo = guild.get_channel(int(res_antigo[1])) or await guild.fetch_channel(int(res_antigo[1]))
            if canal_antigo:
                try:
                    msg_painel_velha = await canal_antigo.fetch_message(int(res_antigo[0]))
                    await msg_painel_velha.delete()
                except: pass
                if res_antigo[2]:
                    try:
                        msg_aviso_velha = await canal_antigo.fetch_message(int(res_antigo[2]))
                        await msg_aviso_velha.delete()
                    except: pass
        except: pass
    # ------------------------------------------------------------------------------

    cursor.execute("SELECT dados_vagas FROM presets WHERE guild_id = ? AND nome_preset = ?", (str(guild.id), nome_preset))
    res_preset = cursor.fetchone()

    cursor.execute("DELETE FROM categorias_sistema WHERE guild_id = ?", (str(guild.id),))
    conn.commit()

    if res_preset:
        vagas_salvas = json.loads(res_preset[0])
        for cat in CATEGORIAS_PADRAO_INICIAIS:
            limite = vagas_salvas.get(cat, 0)
            cursor.execute("INSERT OR REPLACE INTO categorias_sistema (guild_id, classe, limite) VALUES (?, ?, ?)", (str(guild.id), cat, int(limite)))
        conn.commit()
    else:
        for cat in CATEGORIAS_PADRAO_INICIAIS:
            cursor.execute("INSERT OR REPLACE INTO categorias_sistema (guild_id, classe, limite) VALUES (?, ?, 0)", (str(guild.id), cat))
        conn.commit()

    limites_carregados = carregar_categorias_db(guild.id)
    presencas_ativas = {cat: [] for cat in limites_carregados.keys()}
    wait_list_geral.clear()

    cursor.execute("SELECT cargo_membro_id FROM config WHERE guild_id = ?", (str(guild.id),))
    res_cargo = cursor.fetchone()
    
    if res_cargo and res_cargo[0] and str(res_cargo[0]).strip() != "":
        cargo_id_str = str(res_cargo[0]).strip()
    else:
        cursor.execute("SELECT cargo_membro_id FROM config LIMIT 1")
        fallback = cursor.fetchone()
        cargo_id_str = str(fallback[0]).strip() if fallback and fallback[0] else None

    mencao = f"<@&{cargo_id_str}>" if cargo_id_str else "@here"
    
    view = GradeBotoesView(guild.id)
    embed_visual = gerar_texto_painel(guild.id, guild)
    embed_visual.set_footer(text=f"Estratégia aplicada: Preset [{nome_preset}]")

    msg_painel = await canal.send(embed=embed_visual, view=view)
    msg_aviso = await canal.send(f"{mencao} ⚔️ **PAINEL DE GUERRA ABERTO!**")

    # MÁGICA SALVA-VIDAS: Garante 100% que os IDs da mensagem sejam salvos no banco
    cursor.execute("SELECT guild_id FROM config WHERE guild_id = ?", (str(guild.id),))
    if cursor.fetchone():
        cursor.execute("""
            UPDATE config 
            SET painel_msg_id = ?, canal_automacao_id = ?, aviso_msg_id = ?
            WHERE guild_id = ?
        """, (str(msg_painel.id), str(canal.id), str(msg_aviso.id), str(guild.id)))
    else:
        cursor.execute("""
            INSERT INTO config (guild_id, painel_msg_id, canal_automacao_id, aviso_msg_id)
            VALUES (?, ?, ?, ?)
        """, (str(guild.id), str(msg_painel.id), str(canal.id), str(msg_aviso.id)))
        
    conn.commit()
    conn.close()

    async def enviar_dms_background(todos_membros, cargo_alvo_id, canal_alvo):
        if not cargo_alvo_id: return
        for membro in todos_membros:
            if membro.bot: continue
            if any(str(role.id) == str(cargo_alvo_id) for role in getattr(membro, 'roles', [])):
                try: 
                    await membro.send(f"⚔️ O Painel para a **GUERRA** foi aberto no canal {canal_alvo.mention}!")
                    await asyncio.sleep(0.5)
                except Exception: pass

    asyncio.create_task(enviar_dms_background(guild.members, cargo_id_str, canal))

async def ejecutar_encerramento_sistema(guild, canal_fallback):
    global presencas_ativas
    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()
    
    cursor.execute("SELECT painel_msg_id, aviso_msg_id, canal_automacao_id FROM config WHERE guild_id = ?", (str(guild.id),))
    res = cursor.fetchone()

    if res:
        painel_id = res[0]
        aviso_id = res[1]
        canal_id = res[2]
        
        # MÁGICA DA LIMPEZA: Busca robusta do canal para não depender do cache do bot
        canal_alvo = None
        if canal_id and str(canal_id) != "None" and str(canal_id).strip() != "":
            try:
                canal_alvo = guild.get_channel(int(canal_id)) or await guild.fetch_channel(int(canal_id))
            except Exception:
                pass
        
        if not canal_alvo:
            canal_alvo = canal_fallback

        if painel_id and str(painel_id) != "None" and str(painel_id).strip() != "":
            try: 
                msg = await canal_alvo.fetch_message(int(painel_id))
                await msg.delete()
            except Exception: pass
        
        if aviso_id and str(aviso_id) != "None" and str(aviso_id).strip() != "":
            try: 
                msg_aviso = await canal_alvo.fetch_message(int(aviso_id))
                await msg_aviso.delete()
            except Exception: pass

    data_hoje = datetime.now(BR_TIMEZONE).strftime("%Y-%m-%d")
    for funcao, membros in presencas_ativas.items():
        for uid in membros:
            cursor.execute("INSERT OR REPLACE INTO historico_presenca (guild_id, user_id, data_evento, funcao) VALUES (?, ?, ?, ?)", (str(guild.id), str(uid), data_hoje, funcao))

    cursor.execute("UPDATE config SET painel_msg_id = NULL, aviso_msg_id = NULL WHERE guild_id = ?", (str(guild.id),))
    conn.commit()
    conn.close()

    limites_carregados = carregar_categorias_db(guild.id)
    presencas_ativas = {cat: [] for cat in limites_carregados.keys()}
    wait_list_geral.clear()

    aviso_fim = await canal_fallback.send("🛑 **A GUERRA foi encerrada! O painel foi fechado e o histórico salvo.**")
    
    async def apagar_aviso(msg):
        await asyncio.sleep(120)
        try: await msg.delete()
        except Exception: pass
        
    asyncio.create_task(apagar_aviso(aviso_fim))

async def compilar_e_enviar_relatorios(guild, canal_destino):
    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()

    data_limite = (datetime.now(BR_TIMEZONE) - timedelta(days=7)).strftime("%Y-%m-%d")

    cursor.execute("""
        SELECT user_id, COUNT(*) as qtd 
        FROM historico_presenca 
        WHERE guild_id = ? AND data_evento >= ?
        GROUP BY user_id 
        ORDER BY qtd DESC
    """, (str(guild.id), data_limite))
    dados_frequencia = cursor.fetchall()

    cursor.execute("SELECT cargo_membro_id FROM config WHERE guild_id = ?", (str(guild.id),))
    res_cargo = cursor.fetchone()

    cargo_id_membro = int(res_cargo[0]) if res_cargo and res_cargo[0] else None
    cargo_membro = guild.get_role(cargo_id_membro) if cargo_id_membro else None

    usuarios_com_presenca = [str(linha[0]) for linha in dados_frequencia]
    lista_ausentes = []

    membros_alvo = cargo_membro.members if cargo_membro else guild.members
    for m in membros_alvo:
        if not m.bot and str(m.id) not in usuarios_com_presenca:
            lista_ausentes.append(m.mention)

    conn.close()

    tabela_linhas = []
    for uid, qtd in dados_frequencia:
        mb = guild.get_member(int(uid))
        nome_usuario = mb.display_name if mb else f"ID: {uid}"
        tabela_linhas.append([nome_usuario, f"{qtd} War(s)"])

    tabela_formatada = tabulate(tabela_linhas, headers=["Membro da Guilda", "Participações"], tablefmt="simple") if tabela_linhas else "Nenhum registro encontrado nos últimos 7 dias."

    embed = discord.Embed(
        title="📊 RELATÓRIO SEMANAL AUTOMÁTICO DE GUERRAS",
        description="Compilado estratégico com base nas atividades e listas de presença dos últimos 7 dias.",
        color=discord.Color.purple()
    )

    msg_tabela = "```\n" + str(tabela_formatada) + "\n```"

    if len(msg_tabela) > 1024:
        embed.add_field(name="📈 Frequência Semanal de Membros", value="⚠️ Lista muito longa! Enviada no corpo do chat separadamente.", inline=False)
        await canal_destino.send(embed=embed)
        await canal_destino.send(f"**📈 Frequência Semanal de Membros:**\n{msg_tabela}")
        embed = discord.Embed(color=discord.Color.purple())
    else:
        embed.add_field(name="📈 Frequência Semanal de Membros", value=msg_tabela, inline=False)

    texto_ausentes = ", ".join(lista_ausentes) if lista_ausentes else "*Nenhum membro ausente! 100% de atividade.*"
    if len(texto_ausentes) > 1024:
        texto_ausentes = texto_ausentes[:1000] + "... e outros membros."

    embed.add_field(name="🛑 Ausentes (0 participações nos últimos 7 dias)", value=texto_ausentes, inline=False)
    embed.set_footer(text=f"Emissão automática: {datetime.now(BR_TIMEZONE).strftime('%d/%m/%Y %H:%M')}")

    await canal_destino.send(embed=embed)

@tasks.loop(minutes=1)
async def verificador_horarios_loop():
    agora = datetime.now(BR_TIMEZONE)
    horario_atual_str = agora.strftime("%H:%M")
    amanha = agora + timedelta(days=1)
    dia_guerra_alvo = amanha.weekday()

    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()
    cursor.execute("SELECT guild_id, canal_automacao_id, hora_criar, hora_encerrar FROM config")
    configs = cursor.fetchall()

    for g_id, c_id, h_criar, h_encerrar in configs:
        if not c_id: continue
        if verificar_status_automacao(g_id) == 0: continue

        h_criar = h_criar or "22:10"
        h_encerrar = h_encerrar or "22:05"

        guild = bot.get_guild(int(g_id))
        canal = guild.get_channel(int(c_id)) if guild else None
        if not guild or not canal: continue

        if horario_atual_str == h_encerrar:
            cursor.execute("SELECT painel_msg_id FROM config WHERE guild_id = ?", (g_id,))
            p_msg = cursor.fetchone()
            if p_msg and p_msg[0]:
                await ejecutar_encerramento_sistema(guild, canal)

        if horario_atual_str == h_criar:
            cursor.execute("SELECT nome_preset, ativo FROM cronograma_semanal WHERE guild_id = ? AND dia_guerra = ?", (g_id, dia_guerra_alvo))
            crono = cursor.fetchone()
            preset_usar = crono[0] if crono else "t2-40"
            ativo = crono[1] if crono else 1

            if ativo == 1:
                await ejecutar_criacao_sistema(guild, canal, preset_usar)

    conn.close()

@tasks.loop(minutes=1)
async def relatorio_semanal_loop():
    agora = datetime.now(BR_TIMEZONE)

    if agora.weekday() == 5 and agora.strftime("%H:%M") == "12:00":
        conn = sqlite3.connect("guild_nodewar.db")
        cursor = conn.cursor()
        cursor.execute("SELECT guild_id, canal_staff_id FROM config")
        dados = cursor.fetchall()
        conn.close()

        for g_id, c_staff_id in dados:
            if not c_staff_id: continue
            if verificar_status_automacao(g_id) == 0: continue

            guild = bot.get_guild(int(g_id))
            canal_staff = guild.get_channel(int(c_staff_id)) if guild else None
            if guild and canal_staff:
                await compilar_e_enviar_relatorios(guild, canal_staff)

@bot.tree.command(name="config_canal_staff", description="📊 Define o canal privado da Staff onde os relatórios de sábado serão entregues.")
async def config_canal_staff_cmd(interaction: discord.Interaction, canal: discord.TextChannel):
    # Trava de segurança híbrida (Admin ou Cargo Staff)
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao:
        return await interaction.response.send_message("❌ Acesso Negado! Apenas a Staff pode usar este comando.", ephemeral=True)
    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE config SET canal_staff_id = ? WHERE guild_id = ?", (str(canal.id), str(interaction.guild.id)))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ Canal de Relatórios configurado com sucesso! Os relatórios serão postados in {canal.mention} todo sábado ao meio-dia.")

@bot.tree.command(name="categoria_adicionar", description="🆕 Cria uma categoria de botão inteiramente nova para o painel.")
async def categoria_adicionar_cmd(interaction: discord.Interaction, nome_categoria: str):
    # Trava de segurança híbrida (Admin ou Cargo Staff)
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao:
        return await interaction.response.send_message("❌ Acesso Negado! Apenas a Staff pode usar este comando.", ephemeral=True)
    nome_limpo = nome_categoria.strip().upper()
    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO categorias_sistema (guild_id, classe, limite) VALUES (?, ?, 0)", (str(interaction.guild.id), nome_limpo))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ Categoria **{nome_limpo}** criada com sucesso! Use `/vaga` para abrir vagas.")

@bot.tree.command(name="categoria_remover", description="🗑️ Exclui permanentemente uma categoria do sistema.")
async def categoria_remover_cmd(interaction: discord.Interaction, nome_categoria: str):
    # Trava de segurança híbrida (Admin ou Cargo Staff)
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao:
        return await interaction.response.send_message("❌ Acesso Negado! Apenas a Staff pode usar este comando.", ephemeral=True)
    nome_limpo = nome_categoria.strip().upper()
    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM categorias_sistema WHERE guild_id = ? AND classe = ?", (str(interaction.guild.id), nome_limpo))
    cursor.execute("DELETE FROM requisitos_classes WHERE guild_id = ? AND classe = ?", (str(interaction.guild.id), nome_limpo))
    conn.commit()
    conn.close()
    if nome_limpo in presencas_ativas: del presencas_ativas[nome_limpo]
    await atualizar_painel_existente(interaction.guild)
    await interaction.response.send_message(f"🗑️ Categoria **{nome_limpo}** foi completamente deletada do sistema.")

@bot.tree.command(name="switch_automacao", description="🛑 LIGA/DESLIGA completamente todas as automações e DMs do Bot.")
@app_commands.choices(status=[app_commands.Choice(name="Ligar Automações (Ativo)", value=1), app_commands.Choice(name="Desligar Automações (Pausado)", value=0)])
async def switch_automacao_cmd(interaction: discord.Interaction, status: int):
    # Trava de segurança híbrida (Admin ou Cargo Staff)
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao:
        return await interaction.response.send_message("❌ Acesso Negado! Apenas a Staff pode usar este comando.", ephemeral=True)
    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO status_global (guild_id, automacao_ativa) VALUES (?, ?)", (str(interaction.guild.id), status))
    conn.commit()
    conn.close()
    if status == 1: await interaction.response.send_message("▶️ **SISTEMA ATIVADO!**")
    else: await interaction.response.send_message("⏸️ **SISTEMA PAUSADO!**")

@bot.tree.command(name="config_cargo_classe", description="🔒 Define uma tag obrigatória para poder se inscrever in uma categoria.")
async def config_cargo_classe_cmd(interaction: discord.Interaction, classe: str, cargo: discord.Role = None):
    # Trava de segurança híbrida (Admin ou Cargo Staff)
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao:
        return await interaction.response.send_message("❌ Acesso Negado! Apenas a Staff pode usar este comando.", ephemeral=True)
    cat_nome = classe.strip().upper()
    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()
    if cargo is None:
        cursor.execute("DELETE FROM requisitos_classes WHERE guild_id = ? AND classe = ?", (str(interaction.guild.id), cat_nome))
        msg = f"🔓 O requisito para **{cat_nome}** foi removido!"
    else:
        cursor.execute("INSERT INTO requisitos_classes (guild_id, classe, cargo_id) VALUES (?, ?, ?) ON CONFLICT(guild_id, classe) DO UPDATE SET cargo_id = excluded.cargo_id", (str(interaction.guild.id), cat_nome, str(cargo.id)))
        msg = f"🔒 Sucesso! Apenas membros com {cargo.mention} podem pegar vaga em **{cat_nome}**."
    conn.commit()
    conn.close()
    await atualizar_painel_existente(interaction.guild)
    await interaction.response.send_message(msg)

@bot.tree.command(name="config_automacao", description="Define a sala para as postagens automáticas.")
async def config_automacao_cmd(interaction: discord.Interaction, canal: discord.TextChannel):
    # Trava de segurança híbrida (Admin ou Cargo Staff)
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao:
        return await interaction.response.send_message("❌ Acesso Negado! Apenas a Staff pode usar este comando.", ephemeral=True)
    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()
    cursor.execute("SELECT guild_id FROM config WHERE guild_id = ?", (str(interaction.guild.id),))
    if cursor.fetchone(): cursor.execute("UPDATE config SET canal_automacao_id = ? WHERE guild_id = ?", (str(canal.id), str(interaction.guild.id)))
    else: cursor.execute("INSERT INTO config (guild_id, canal_automacao_id, hora_criar, hora_encerrar) VALUES (?, ?, '22:10', '22:05')", (str(interaction.guild.id), str(canal.id)))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ Sala definida com sucesso: {canal.mention}")

@bot.tree.command(name="cargopresenca", description="Define o cargo de Membro na guilda.")
async def cargopresenca_cmd(interaction: discord.Interaction, cargo: discord.Role):
    # Trava de segurança híbrida (Admin ou Cargo Staff)
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao:
        return await interaction.response.send_message("❌ Acesso Negado! Apenas a Staff pode usar este comando.", ephemeral=True)
    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()
    cursor.execute("SELECT guild_id FROM config WHERE guild_id = ?", (str(interaction.guild.id),))
    if cursor.fetchone(): cursor.execute("UPDATE config SET cargo_membro_id = ? WHERE guild_id = ?", (str(cargo.id), str(interaction.guild.id)))
    else: cursor.execute("INSERT INTO config (guild_id, cargo_membro_id, hora_criar, hora_encerrar) VALUES (?, ?, '22:10', '22:05')", (str(interaction.guild.id), str(cargo.id)))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ Cargo mapeado com sucesso: {cargo.mention}")

@bot.tree.command(name="vaga", description="Define o limite de vagas de uma categoria. Digite 0 para ocultá-la.")
async def vaga_cmd(interaction: discord.Interaction, botao: str, limite: int):
    # Trava de segurança híbrida (Admin ou Cargo Staff)
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao:
        return await interaction.response.send_message("❌ Acesso Negado! Apenas a Staff pode usar este comando.", ephemeral=True)
    cat_nome = botao.strip().upper()
    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO categorias_sistema (guild_id, classe, limite) VALUES (?, ?, ?)", (str(interaction.guild.id), cat_nome, limite))
    conn.commit()
    conn.close()
    await atualizar_painel_existente(interaction.guild)
    if limite <= 0: await interaction.response.send_message(f"🚫 Categoria **{cat_nome}** foi configurada para 0 vagas e sumirá do painel.")
    else: await interaction.response.send_message(f"✅ Vagas de **{cat_nome}** configuradas para **{limite}**.")

@bot.tree.command(name="salvar_preset_atual", description="Salva as vagas atuais como um preset.")
async def salvar_preset_atual_cmd(interaction: discord.Interaction, nome_preset: str):
    # Trava de segurança híbrida (Admin ou Cargo Staff)
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao:
        return await interaction.response.send_message("❌ Acesso Negado! Apenas a Staff pode usar este comando.", ephemeral=True)
    limites = carregar_categorias_db(interaction.guild.id)
    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO presets (guild_id, nome_preset, dados_vagas) VALUES (?, ?, ?)", (str(interaction.guild.id), nome_preset, json.dumps(limites)))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ Preset **{nome_preset}** salvo com sucesso!")

@bot.tree.command(name="ver_cronograma", description="Exibe os dias de GUERRA ativos.")
async def ver_cronograma_cmd(interaction: discord.Interaction):
    configurar_dias_padrao(interaction.guild.id)
    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()
    cursor.execute("SELECT dia_guerra, nome_preset, ativo FROM cronograma_semanal WHERE guild_id = ? ORDER BY dia_guerra", (str(interaction.guild.id),))
    linhas = cursor.fetchall()
    conn.close()
    tabela = [[DIAS_DA_SEMANA_PT[dia], preset, "ATIVO" if ativo == 1 else "INATIVO"] for dia, preset, ativo in linhas]
    res_tabela = tabulate(tabela, headers=["Dia da Guerra", "Preset Usado", "Status"], tablefmt="simple")
    mensagem_formatada = f"```\n{res_tabela}\n```"
    await interaction.response.send_message(mensagem_formatada)

@bot.tree.command(name="config_dia_war", description="Liga/Desliga a automação baseando-se no dia da GUERRA.")
@app_commands.choices(dia=[app_commands.Choice(name=v, value=k) for k, v in DIAS_DA_SEMANA_PT.items()], status=[app_commands.Choice(name="Ligado", value=1), app_commands.Choice(name="Desligado", value=0)])
async def config_dia_war_cmd(interaction: discord.Interaction, dia: int, status: int):
    # Trava de segurança híbrida (Admin ou Cargo Staff)
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao:
        return await interaction.response.send_message("❌ Acesso Negado! Apenas a Staff pode usar este comando.", ephemeral=True)
    configurar_dias_padrao(interaction.guild.id)
    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE cronograma_semanal SET ativo = ? WHERE guild_id = ? AND dia_guerra = ?", (status, str(interaction.guild.id), dia))
    conn.commit()
    conn.close()
    status_str = "Ligado" if status == 1 else "Desligado"
    await interaction.response.send_message(f"✅ A GUERRA de **{DIAS_DA_SEMANA_PT[dia]}** configurada como: **{status_str}**.")

@bot.tree.command(name="config_cronograma", description="Associa um preset ao dia da GUERRA.")
@app_commands.choices(dia=[app_commands.Choice(name=v, value=k) for k, v in DIAS_DA_SEMANA_PT.items()])
async def config_cronograma_cmd(interaction: discord.Interaction, dia: int, preset: str):
    # Trava de segurança híbrida (Admin ou Cargo Staff)
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao:
        return await interaction.response.send_message("❌ Acesso Negado! Apenas a Staff pode usar este comando.", ephemeral=True)
    configurar_dias_padrao(interaction.guild.id)
    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE cronograma_semanal SET nome_preset = ? WHERE guild_id = ? AND dia_guerra = ?", (preset, str(interaction.guild.id), dia))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ A GUERRA de **{DIAS_DA_SEMANA_PT[dia]}** usará o preset: **{preset}**.")

@bot.tree.command(name="config_horarios", description="Altera o horário de abertura/fechamento.")
async def config_horarios_cmd(interaction: discord.Interaction, hora_criar: str, hora_encerrar: str):
    # Trava de segurança híbrida (Admin ou Cargo Staff)
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao:
        return await interaction.response.send_message("❌ Acesso Negado! Apenas a Staff pode usar este comando.", ephemeral=True)
    import re
    formato_hora = re.compile(r"^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$")
    if not formato_hora.match(hora_criar) or not formato_hora.match(hora_encerrar): return await interaction.response.send_message("❌ Formato inválido! Use `22:10`", ephemeral=True)
    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE config SET hora_criar = ?, hora_encerrar = ? WHERE guild_id = ?", (hora_criar, hora_encerrar, str(interaction.guild.id)))
    conn.commit()
    conn.close()
    await interaction.response.send_message("Campanha de horários configurada com sucesso!", ephemeral=True)

@bot.tree.command(name="guerra_criar", description="Força abertura manual de painel.")
async def guerra_criar_cmd(interaction: discord.Interaction, preset: str = "t2-40"):
    # Trava de segurança híbrida (Admin ou Cargo Staff)
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao:
        return await interaction.response.send_message("❌ Acesso Negado! Apenas a Staff pode usar este comando.", ephemeral=True)
    await interaction.response.send_message(f"⚙️ Abrindo painel manual...", ephemeral=True)
    await ejecutar_criacao_sistema(interaction.guild, interaction.channel, preset)

@bot.tree.command(name="guerra_encerrar", description="Força fechamento manual do painel.")
async def guerra_encerrar_cmd(interaction: discord.Interaction):
    # Trava de segurança híbrida (Admin ou Cargo Staff)
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao:
        return await interaction.response.send_message("❌ Acesso Negado! Apenas a Staff pode usar este comando.", ephemeral=True)
    await interaction.response.send_message("🛑 Encerrando painel...", ephemeral=True)
    await ejecutar_encerramento_sistema(interaction.guild, interaction.channel)

@bot.tree.command(name="forcar_presenca", description="💥 Adiciona ou altera manualmente a classe de um membro no painel ativo.")
async def forcar_presenca_cmd(interaction: discord.Interaction, membro: discord.Member, classe: str):
    # Trava de segurança híbrida (Admin ou Cargo Staff)
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao:
        return await interaction.response.send_message("❌ Acesso Negado! Apenas a Staff pode usar este comando.", ephemeral=True)
    cat_nome = classe.strip()
    global wait_list_geral
    if cat_nome not in presencas_ativas: presencas_ativas[cat_nome] = []
    for c in list(presencas_ativas.keys()):
        if membro.id in presencas_ativas[c]:
            presencas_ativas[c].remove(membro.id)
            for i, j in enumerate(wait_list_geral):
                if j["funcao"] == c:
                    promovido = wait_list_geral.pop(i)
                    if c not in presencas_ativas: presencas_ativas[c] = []
                    presencas_ativas[c].append(promovido["user_id"])
                    asyncio.create_task(notificar_promovido_dm(interaction.client, promovido["user_id"], interaction.guild.id, c))
                    break
    wait_list_geral = [w for w in wait_list_geral if w["user_id"] != membro.id]
    presencas_ativas[cat_nome].append(membro.id)
    await atualizar_painel_existente(interaction.guild)
    await interaction.response.send_message(f"✅ Presença de {membro.mention} forçada em **{cat_nome}**!", ephemeral=True)

@bot.tree.command(name="ux_puxar_relatorio", description="📊 Força a geração manual imediata dos relatórios semanais de presença e de membros ausentes.")
async def ux_puxar_relatorio_cmd(interaction: discord.Interaction):
    # Trava de segurança híbrida (Admin ou Cargo Staff)
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao:
        return await interaction.response.send_message("❌ Acesso Negado! Apenas a Staff pode usar este comando.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    await compilar_e_enviar_relatorios(interaction.guild, interaction.channel)
    await interaction.followup.send("📊 Relatório compilado com sucesso e enviado neste canal!")

@bot.tree.command(name="help", description="🎯 Exibe o Painel Supremo de Auditoria e o Manual Técnico Completo da Staff.")
async def help_cmd(interaction: discord.Interaction):
    # Trava de segurança híbrida (Admin ou Cargo Staff)
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao:
        return await interaction.response.send_message("❌ Acesso Negado! Apenas a Staff pode usar este comando.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT cargo_membro_id, canal_automacao_id, canal_staff_id, hora_criar, hora_encerrar FROM config LIMIT 1")
        config_db = cursor.fetchone()
    except Exception:
        config_db = None

    try:
        cursor.execute("SELECT dia_guerra, nome_preset, ativo FROM cronograma_semanal ORDER BY dia_guerra ASC")
        crono_linhas = cursor.fetchall()
    except Exception:
        crono_linhas = []

    try:
        cursor.execute("SELECT classe, cargo_id FROM requisitos_classes")
        req_linhas = {str(linha[0]).strip().lower(): linha[1] for linha in cursor.fetchall()}
    except Exception:
        req_linhas = {}

    conn.close()

    if config_db:
        c_membro = f"<@&{config_db[0]}>" if config_db[0] and str(config_db[0]).strip() != "" else "❌ `Não configurado`"
        c_auto = f"<#{config_db[1]}>" if config_db[1] and str(config_db[1]).strip() != "" else "❌ `Não configurado`"
        c_staff = f"<#{config_db[2]}>" if config_db[2] and str(config_db[2]).strip() != "" else "❌ `Não configurado`"
        h_criar = config_db[3] if config_db[3] and str(config_db[3]).strip() != "" else "22:10"
        h_encerrar = config_db[4] if config_db[4] and str(config_db[4]).strip() != "" else "22:05"
    else:
        c_membro = c_auto = c_staff = "❌ `Não configurado`"
        h_criar, h_encerrar = "22:10", "22:05"

    status_auto = "🟢 `ATIVO` (Monitorando Horários)"

    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT msg_dm FROM config_mensagens LIMIT 1")
        res_dm = cursor.fetchone()
        msg_abertura_real = res_dm[0] if res_dm and res_dm[0] else "⚔️ O Painel para a **GUERRA** de amanhã já está aberto no canal {canal.mention}!"
    except Exception:
        msg_abertura_real = "⚔️ O Painel para a **GUERRA** de amanhã já está aberto no canal {canal.mention}!"
    conn.close()

    msg_abertura_formatada = msg_abertura_real.replace("{canal}", c_auto).replace("{canal.mention}", c_auto)

    msg_promo_real = (
        "Você foi promovido da fila de espera e convocado para a GUERRA! "
        "Garanta sua participação dentro do jogo!\n"
        "Qualquer dúvida, entre em contato com algum @Staff."
    )

    msg_aberto_bloco = f"```\n{msg_abertura_formatada.replace('`', '')}\n```"
    msg_promo_bloco = f"```\n{msg_promo_real.replace('`', '')}\n```"

    embed_params = discord.Embed(
        title="👑 G59 | PAINEL SUPREMO DE AUDITORIA",
        description="⚙️ **Verificação e status operacional das configurações globais do banco de dados.**",
        color=discord.Color.from_rgb(255, 215, 0)
    )
    embed_params.add_field(
        name="🌐 Infraestrutura Core", 
        value=f"🔹 **Status da Automação:** {status_auto}\n🔹 **Cargo Oficial de Membro:** {c_membro}\n🔹 **Canal do Painel de War:** {c_auto}\n🔹 **Canal de Logs da Staff:** {c_staff}", 
        inline=False
    )
    embed_params.add_field(
        name="⏳ Cronometragem e Loops", 
        value=f"⏰ **Abertura do Painel:** `{h_criar}` *(Gera as inscrições automáticas)*\n⏰ **Fechamento do Painel:** `{h_encerrar}` *(Computa a presença automaticamente)*\n📅 **Relatório de Frequência:** Todo Sábado às `12:00` enviado em {c_staff}", 
        inline=False
    )
    embed_params.add_field(
        name="💬 Comunicação e Notificações (DM)",
        value=f"📢 **Notificação de Abertura:**\n{msg_aberto_bloco}\n\n⏳ **Aviso de Promoção (Lista de Espera):**\n{msg_promo_bloco}",
        inline=False
    )

    embed_crono = discord.Embed(
        title="🗓️ ESCALA E DISTRIBUIÇÃO SEMANAL DE PRESETS",
        description="Planejamento estratégico de quais vagas serão injetadas e aplicadas no dia real de cada guerra.",
        color=discord.Color.blue()
    )

    dias_nomes = ["Domingo", "Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado"]
    dias_emojis = ["👑", "🌙", "🔥", "🌊", "⚡", "⚔️", "🪵"]

    cronograma_mapeado = {}
    for linha in crono_linhas:
        try:
            dia_real_guerra = (int(linha[0]) + 1) % 7
            cronograma_mapeado[dia_real_guerra] = {"preset": str(linha[1]), "ativo": int(linha[2])}
        except Exception:
            continue

    for dia_idx, nome_dia in enumerate(dias_nomes):
        if dia_idx in cronograma_mapeado:
            preset_nome = cronograma_mapeado[dia_idx]["preset"]
            ativo = cronograma_mapeado[dia_idx]["ativo"]

            if ativo == 1:
                status_dia = f"⚙️ Estratégia Aplicada: `{preset_nome.upper()}`\n┗ 🟢 *Automação de abertura confirmada para este dia.*"
            else:
                status_dia = f"⚙️ Estratégia Aplicada: `{preset_nome.upper()}`\n┗ 🛑 *Folga da Guilda (Loops suspensos).*"
        else:
            status_dia = "⚙️ Estratégia Aplicada: `T2-40` *(Padrão)*\n┗ 🟡 *Nenhum preset alternativo foi salvo.*"

        embed_crono.add_field(name=f"{dias_emojis[dia_idx]} {nome_dia}", value=status_dia, inline=False)

    embed_cats = discord.Embed(
        title="🔒 REQUISITOS E TRAVAS DE CATEGORIAS",
        description="Filtro de tags obrigatórias configuradas para impedir que membros sem o cargo correto assumam a vaga.",
        color=discord.Color.red()
    )

    texto_cats = ""
    for classe_com_emoji in CATEGORIAS_PADRAO_INICIAIS:
        partes = classe_com_emoji.split(" ", 1)
        classe_limpa = partes[1].strip() if len(partes) > 1 else classe_com_emoji.strip()

        cargo_id = None
        for tentativa in [classe_com_emoji.lower().strip(), classe_limpa.lower().strip()]:
            if tentativa in req_linhas:
                cargo_id = req_linhas[tentativa]
                break

        mencao_cargo = f"<@&{cargo_id}>" if cargo_id else "🔓 *Livre para todos os membros*"
        texto_cats += f"• {classe_com_emoji} ➔ Requisito: {mencao_cargo}\n"

    embed_cats.add_field(name="🛡️ Mapeamento de Permissões por Botão", value=texto_cats, inline=False)

    embed_comandos = discord.Embed(
        title="📖 GUIA COMPLETO DE COMANDOS DO SISTEMA",
        description="Todos os comandos de barra integrados que a Staff pode utilizar para gerenciar a guilda de forma individualizada e scannável.",
        color=discord.Color.purple()
    )

    embed_comandos.add_field(
        name="🛠️ 1. Infraestrutura Core",
        value=(
            "🔹 `/setup_presets`\n"
            "┗ Injeta automaticamente os 4 presets oficiais de guerra no banco de dados do servidor.\n"
            "🔹 `/config_automacao [canal]`\n"
            "┗ Define a sala onde o painel público com botões será aberto e atualizado.\n"
            "🔹 `/cargopresenca [cargo]`\n"
            "┗ Vincula o cargo oficial de membro. O bot usará essa tag para marcar no canal e disparar avisos na DM.\n"
            "🔹 `/config_canal_staff [canal]`\n"
            "┗ Configura a sala restrita da Staff para onde os relatórios consolidados de sábado serão enviados.\n"
            "🔹 `/config_msg_dm [nova_mensagem]`\n"
            "┗ Altera o texto de aviso enviado no privado dos membros (use {canal} para marcar a sala).\n"
            "🔹 `/switch_automacao [status]`\n"
            "┗ Ativa ou pausa globalmente os loops de tempo. Se pausado, nada abre ou fecha sozinho."
        ),
        inline=False
    )

    embed_comandos.add_field(
        name="⚙️ 2. Customização e Custom Buttons",
        value=(
            "🔹 `/categoria_adicionar [nome_categoria]`\n"
            "┗ Injeta um botão de classe totalmente novo no banco de dados para aparecer nas próximas listagens.\n"
            "🔹 `/categoria_remover [nome_categoria]`\n"
            "┗ Exclui permanentemente um botão do sistema, eliminando também seus registros de cargos.\n"
            "🔹 `/config_cargo_classe [classe] [cargo]`\n"
            "┗ Tranca uma função específica. Apenas membros portando a tag escolhida poderão clicar no botão.\n"
            "🔹 `/vaga [botao] [limite]`\n"
            "┗ Altera o teto numérico de vagas de um botão ativo. Definir para `0` oculta o botão temporariamente.\n"
            "🔹 `/salvar_preset_atual [nome_preset]`\n"
            "┗ Captura a atual estrutura de vagas ativas do painel e consolida como uma estratégia reutilizável."
        ),
        inline=False
    )

    embed_comandos.add_field(
        name="🗓️ 3. Escala Semanal e Cronograma",
        value=(
            "🔹 `/config_horarios [hora_criar] [hora_encerrar]`\n"
            "┗ Modifica os gatilhos dos loops diários. (Ex: Abertura às `22:10` e Fechamento às `22:05`).\n"
            "🔹 `/config_dia_war [dia] [status]`\n"
            "┗ Permite ativar ou suspender o funcionamento automatizado de um dia específico na rotina da semana.\n"
            "🔹 `/config_cronograma [dia] [preset]`\n"
            "┗ Associa qual preset de vagas salvo deve ser injetado de forma automática no dia escolhido.\n"
            "🔹 `/ver_cronograma`\n"
            "┗ Gera uma visualização limpa em formato de tabela listando todos os dias, presets e se estão ativos."
        ),
        inline=False
    )

    embed_comandos.add_field(
        name="🚨 4. Operação Manual e Emergências",
        value=(
            "🔹 `/guerra_criar [preset]`\n"
            "┗ Força a abertura manual imediata de um painel de inscrições no canal, aplicando as regras do preset.\n"
            "🔹 `/guerra_encerrar`\n"
            "┗ Força o fechamento abrupto do painel atual, salvando quem garantiu vaga na tabela de histórico.\n"
            "🔹 `/forcar_presenca [membro] [classe]`\n"
            "┗ Remove um usuário de onde quer que esteja e o coloca arbitrariamente dentro de uma classe ativa.\n"
            "🔹 `/ux_puxar_relatorio`\n"
            "┗ Executa de forma forçada a compilação e envio imediato do Relatório Semanal de Frequência e Ausentes."
        ),
        inline=False
    )

    embed_comandos.set_footer(text=f"Auditoria realizada em: {datetime.now(BR_TIMEZONE).strftime('%d/%m/%Y %H:%M:%S')}")

    await interaction.followup.send(embeds=[embed_params, embed_crono, embed_cats, embed_comandos], ephemeral=True)

@bot.tree.command(name="sync", description="🔄 Força o bot a baixar as novidades da Planilha do Google.")
async def sync_cmd(interaction: discord.Interaction):
    # Trava de Staff
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao:
        return await interaction.response.send_message("❌ Acesso Negado! Apenas a Staff pode usar este comando.", ephemeral=True)
    
    # O bot avisa que está pensando (já que puxar do Google demora uns 2 segundos)
    await interaction.response.defer(ephemeral=True)
    
    sucesso, mensagem = await sincronizar_planilha()
    
    await interaction.followup.send(mensagem)
    
@bot.tree.command(name="setup_presets", description="🛠️ Injeta os 4 presets oficiais e os cargos no banco de dados do servidor.")
async def setup_presets_cmd(interaction: discord.Interaction):
    # Trava de segurança híbrida (Admin ou Cargo Staff)
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao:
        return await interaction.response.send_message("❌ Acesso Negado! Apenas a Staff pode usar este comando.", ephemeral=True)
    
    presets_completos = {
        "t2-40": {
            "👑 CALLER": 3, "👊 STRIKER": 5, "💥 ZERK SUCC": 2, "🏹 ARCHER/RANGER": 9, 
            "🎸 SHAI": 2, "🛡️ NOVA SUCC": 2, "㊙️ DO-SA": 1, "🪶 SUPORTE": 2, 
            "🥷 SCOUT": 4, "⚔️ ATAQUE": 5, "🏳️ BANDEIRA": 1, "🐘 ELEFANTE": 1, "🏛️ DEFESA": 3
        },
        "t2-50": {
            "👑 CALLER": 3, "👊 STRIKER": 6, "💥 ZERK SUCC": 3, "🏹 ARCHER/RANGER": 11, 
            "🎸 SHAI": 3, "🛡️ NOVA SUCC": 2, "㊙️ DO-SA": 3, "🪶 SUPORTE": 2, 
            "🥷 SCOUT": 5, "⚔️ ATAQUE": 7, "🏳️ BANDEIRA": 1, "🐘 ELEFANTE": 1, "🏛️ DEFESA": 3
        },
        "t1-25": {
            "👑 CALLER": 3, "👊 STRIKER": 4, "💥 ZERK SUCC": 1, "🏹 ARCHER/RANGER": 6, 
            "🎸 SHAI": 2, "🛡️ NOVA SUCC": 1, "㊙️ DO-SA": 0, "🪶 SUPORTE": 0, 
            "🥷 SCOUT": 0, "⚔️ ATAQUE": 4, "🏳️ BANDEIRA": 1, "🐘 ELEFANTE": 1, "🏛️ DEFESA": 2
        },
        "t1-30": {
            "👑 CALLER": 3, "👊 STRIKER": 5, "💥 ZERK SUCC": 1, "🏹 ARCHER/RANGER": 8, 
            "🛡️ NOVA SUCC": 2, "🎸 SHAI": 2, "㊙️ DO-SA": 0, "🪶 SUPORTE": 0, 
            "🥷 SCOUT": 0, "⚔️ ATAQUE": 5, "🏳️ BANDEIRA": 1, "🐘 ELEFANTE": 1, "🏛️ DEFESA": 2
        }
    }
    
    import sqlite3, json
    conn = sqlite3.connect("guild_nodewar.db")
    cursor = conn.cursor()

    # --- INJEÇÃO DOS CARGOS FIXOS ---
    # 1. Limpa todas as travas antigas
    cursor.execute("DELETE FROM requisitos_classes")

    # 2. Lista de TODAS as classes blindadas
    cargos_oficiais = [
        ("caller", "1344432495382495264"),
        ("striker", "1488887105768915219"),
        ("zerk succ", "1510840382592651406"),
        ("archer/ranger", "1488544280422256690"),
        ("shai", "1489053211246592031"),
        ("nova succ", "1489053121467383910"),
        ("do-sa", "1510732997928812604"),
        ("suporte", "1510732886180102144"),
        ("scout", "1510839768223711242"),
        ("defesa", "1355009632422334589"),
        ("bandeira", "1355009632422334589"),
        ("elefante", "1355009632422334589")
    ]

    # 3. Salva todos os cargos no banco de dados
    for classe, cargo_id in cargos_oficiais:
        cursor.execute("INSERT INTO requisitos_classes (classe, cargo_id) VALUES (?, ?)", (classe, cargo_id))
    # --------------------------------

    # Salva os presets (T1 e T2)
    for nome_preset, dados_vagas in presets_completos.items():
        cursor.execute("""
            INSERT OR REPLACE INTO presets (guild_id, nome_preset, dados_vagas)
            VALUES (?, ?, ?)
        """, (str(interaction.guild.id), nome_preset, json.dumps(dados_vagas, ensure_ascii=False)))
        
    conn.commit()
    conn.close()
    
    await interaction.response.send_message("✅ Restauração Completa! Os 4 Presets oficiais e TODAS as travas de cargos foram injetados no banco de dados com sucesso!", ephemeral=True)

@bot.event
async def on_ready():
    print(f"✅ Bot conectado como {bot.user}")
    await sincronizar_planilha()
    
    # 🧼 FAXINA DE DUPLICADOS: Limpa o cache local do servidor e sincroniza o global
    try:
        # 1. Limpa os comandos antigos registrados diretamente no nível do servidor
        for guild in bot.guilds:
            bot.tree.clear_commands(guild=guild)
            await bot.tree.sync(guild=guild)
        print("🗑️ Cache de comandos locais duplicados limpo com sucesso!")
        
        # 2. Registra e sincroniza puramente os comandos globais do seu arquivo
        synced = await bot.tree.sync()
        print(f"🔄 {len(synced)} comandos globais oficiais sincronizados.")
    except Exception as e:
        print(f"⚠️ Erro durante a faxina de comandos: {e}")
        
    if not verificador_horarios_loop.is_running(): 
        verificador_horarios_loop.start()
    if not relatorio_semanal_loop.is_running(): 
        relatorio_semanal_loop.start()

keep_alive()
bot.run(os.environ.get("DISCORD_TOKEN"))
