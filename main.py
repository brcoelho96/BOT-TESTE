import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import asyncio
import json
import zoneinfo
import gspread
import os
from flask import Flask
from threading import Thread

# --- MÓDULOS PARA A GAMBIARRA DO RENDER (FLASK) ---
app = Flask('')

@app.route('/')
def home():
    return "⚔️ O Bot da SuicideBoys está ONLINE com Google Sheets e Categorias Dinâmicas!"

def run_web_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server)
    t.start()
# --------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

BR_TIMEZONE = zoneinfo.ZoneInfo("America/Sao_Paulo")

# --- 🧠 MEMÓRIA CACHE DO BOT (Google Sheets) ---
# Agora são dicionários que guardam dicionários: { "ID_DO_SERVIDOR": { dados } }
CACHE_CONFIG = {}
CACHE_CRONOGRAMA = {}
CACHE_PRESETS = {}

# --- ⚡ MEMÓRIA DE EXECUÇÃO RÁPIDA (RAM) ---
RUNTIME = {}

presencas_ativas = {}
wait_list_geral = {}

CATEGORIAS_PADRAO_INICIAIS = [
    "👑 CALLER", "👊 STRIKER", "💥 ZERK SUCC", "🏹 ARCHER/RANGER", 
    "🎸 SHAI", "🛡️ NOVA SUCC", "㊙️ DO-SA", "🪶 SUPORTE", 
    "🥷 SCOUT", "⚔️ ATAQUE", "🏳️ BANDEIRA", "🐘 ELEFANTE", 
    "🏛️ DEFESA"
]

DIAS_DA_SEMANA_PT = {
    0: "Segunda", 1: "Terca", 2: "Quarta", 
    3: "Quinta", 4: "Sexta", 5: "Sabado", 6: "Domingo"
}

# --- 🌐 GPS MULTI-SERVIDOR ---
# Cole aqui dentro das aspas aquele ID gigante da sua "Planilha_Mae_G59"
PLANILHA_MAE_ID = "1as4bbJVJigJE870OvebAepVTduBrofwngKMnHNSLi4A" 

MAPA_PLANILHAS = {}     # Guarda: { "ID_Servidor": "ID_Planilha_Guilda" }
PLANILHAS_ABERTAS = {}  # Guarda a conexão ativa: { "ID_Servidor": objeto_planilha }
gc = None

def iniciar_memoria_servidor(guild_id):
    guild_id_str = str(guild_id)
    if guild_id_str not in RUNTIME:
        RUNTIME[guild_id_str] = {
            "painel_msg_id": None,
            "aviso_msg_id": None,
            "canal_automacao_id": None,
            "limites_atuais": {},  
            "preset_ativo": None,
            "fechado_manualmente": False,
            "relatorio_enviado_hoje": False
        }
    if guild_id_str not in presencas_ativas:
        presencas_ativas[guild_id_str] = {}
    if guild_id_str not in wait_list_geral:
        wait_list_geral[guild_id_str] = []

# --- MOTOR DE BUSCA MULTI-SERVIDOR (GPS) ---
async def obter_planilha_servidor(guild_id):
    guild_id_str = str(guild_id)
    global gc
    
    # Se ainda não fizemos login no Google, fazemos agora
    if not gc:
        try:
            google_creds_json = os.environ.get("GOOGLE_CREDENTIALS")
            creds_dict = json.loads(google_creds_json)
            gc = gspread.service_account_from_dict(creds_dict)
        except Exception as e:
            return None, f"Erro nas credenciais do Google: {e}"

    # 1. Verifica se já temos o ID guardado no Mapa (para não ler a Planilha Mãe toda a hora)
    if guild_id_str not in MAPA_PLANILHAS:
        try:
            planilha_mae = gc.open_by_key(PLANILHA_MAE_ID)
            aba_mae = planilha_mae.sheet1
            dados_mae = aba_mae.get_all_values()
            
            # Procura o servidor na lista
            encontrado = False
            for linha in dados_mae[1:]:
                if len(linha) >= 2 and linha[0].strip() == guild_id_str:
                    MAPA_PLANILHAS[guild_id_str] = linha[1].strip()
                    encontrado = True
                    break
                    
            if not encontrado:
                return None, "❌ Este servidor não está registado na Planilha Mãe."
        except Exception as e:
            return None, f"❌ Erro ao ler a Planilha Mãe: {e}"

    # 2. Agora que sabemos o ID, abrimos a planilha da Guilda
    try:
        if guild_id_str not in PLANILHAS_ABERTAS:
            planilha_guilda = gc.open_by_key(MAPA_PLANILHAS[guild_id_str])
            PLANILHAS_ABERTAS[guild_id_str] = planilha_guilda
        
        return PLANILHAS_ABERTAS[guild_id_str], "Sucesso"
    except Exception as e:
        return None, f"❌ Erro ao aceder à planilha desta guilda: {e}"

# --- CONEXÃO E SINCRONIZAÇÃO ESPECÍFICA POR SERVIDOR ---
async def sincronizar_planilha(guild_id):
    guild_id_str = str(guild_id)
    
    # Prepara a "gaveta" deste servidor na memória RAM
    iniciar_memoria_servidor(guild_id_str)
    
    # Chama o GPS para pegar a planilha certa
    planilha, msg_erro = await obter_planilha_servidor(guild_id_str)
    if not planilha:
        return False, msg_erro

    try:
        # --- CARREGAR CONFIGURAÇÕES ---
        aba_config = planilha.worksheet("Config_Geral")
        dados_config = aba_config.get_all_values()
        
        if guild_id_str not in CACHE_CONFIG: CACHE_CONFIG[guild_id_str] = {}
        CACHE_CONFIG[guild_id_str].clear()
        
        for linha in dados_config[1:]:
            if len(linha) >= 2 and linha[0].strip() != "":
                CACHE_CONFIG[guild_id_str][linha[0].strip()] = linha[1].strip()

        # --- CARREGAR CRONOGRAMA ---
        aba_crono = planilha.worksheet("Cronograma")
        dados_crono = aba_crono.get_all_values()
        
        if guild_id_str not in CACHE_CRONOGRAMA: CACHE_CRONOGRAMA[guild_id_str] = {}
        CACHE_CRONOGRAMA[guild_id_str].clear()
        
        for linha in dados_crono[1:]:
            if len(linha) >= 2 and linha[0].strip() != "":
                CACHE_CRONOGRAMA[guild_id_str][linha[0].strip()] = linha[1].strip()

        # --- CARREGAR PRESETS ---
        aba_presets = planilha.worksheet("Setup_Presets")
        dados_presets = aba_presets.get_all_values()
        
        if guild_id_str not in CACHE_PRESETS: CACHE_PRESETS[guild_id_str] = {}
        CACHE_PRESETS[guild_id_str].clear()
        
        for linha in dados_presets[1:]:
            if len(linha) >= 3 and linha[0].strip() != "":
                nome_preset = linha[0].strip()
                classe = linha[1].strip()
                limite = linha[2].strip()
                travas = linha[3].strip() if len(linha) > 3 else ""
                
                if nome_preset not in CACHE_PRESETS[guild_id_str]:
                    CACHE_PRESETS[guild_id_str][nome_preset] = []
                CACHE_PRESETS[guild_id_str][nome_preset].append({"classe": classe, "limite": limite, "travas": travas})
                
        # Atualiza o canal de automação deste servidor específico na memória
        if "Canal_Painel_ID" in CACHE_CONFIG[guild_id_str]:
            RUNTIME[guild_id_str]["canal_automacao_id"] = CACHE_CONFIG[guild_id_str]["Canal_Painel_ID"]
            
        return True, "✅ Sincronização concluída com o Banco de Dados da sua Guilda!"
    except Exception as e:
        print(f"❌ Erro na Sincronização do Servidor {guild_id_str}: {e}")
        return False, f"❌ Erro ao ler os dados: {e}"

# --- FUNÇÕES DE BASTIDORES (AGORA MULTI-SERVIDOR) ---
async def atualizar_planilha_guerra_background(guild_id):
    guild_id_str = str(guild_id)
    try:
        planilha, _ = await obter_planilha_servidor(guild_id_str)
        if not planilha: return
        aba_guerra = planilha.worksheet("Guerra_Atual")
        linhas = [["ID_Discord", "Nickname", "Classe", "Status"]]
        
        # Puxa apenas as presenças DESTE servidor
        for classe, membros in presencas_ativas.get(guild_id_str, {}).items():
            for user_id in membros:
                linhas.append([str(user_id), "Membro", classe, "Confirmado"])
                
        # Puxa apenas a fila de espera DESTE servidor
        for w in wait_list_geral.get(guild_id_str, []):
            linhas.append([str(w["user_id"]), "Membro", w["funcao"], "Fila de Espera"])
            
        aba_guerra.clear()
        if linhas:
            aba_guerra.append_rows(linhas)
    except Exception as e:
        print(f"⚠️ Erro ao atualizar aba Guerra_Atual (Servidor {guild_id_str}): {e}")

async def enviar_log_staff(guild, mensagem):
    guild_id_str = str(guild.id)
    if guild_id_str not in CACHE_CONFIG: return
    canal_logs_id = CACHE_CONFIG[guild_id_str].get("Canal_Logs_ID")
    if canal_logs_id and str(canal_logs_id).isdigit():
        canal = guild.get_channel(int(canal_logs_id))
        if canal:
            try: await canal.send(f"📋 **Auditoria G59:** {mensagem}")
            except: pass

async def notificar_promovido_dm(bot_client, user_id, guild_id, classe_nome):
    guild_id_str = str(guild_id)
    msg_custom = "Você foi promovido da fila de espera e convocado para a GUERRA! Garanta sua participação."
    if guild_id_str in CACHE_CONFIG:
        msg_custom = CACHE_CONFIG[guild_id_str].get("Msg_Promocao", msg_custom)
    try:
        user = await bot_client.fetch_user(user_id)
        await user.send(msg_custom)
    except Exception: pass

async def notificar_membros_dm(guild, nome_preset):
    guild_id_str = str(guild.id)
    if guild_id_str not in CACHE_CONFIG: return
    texto_dm = CACHE_CONFIG[guild_id_str].get("Msg_DM_Abertura", "⚔️ O Painel para a **GUERRA** já está aberto!")
    cargo_id_str = CACHE_CONFIG[guild_id_str].get("Cargo_Membro_ID", "")
    if not cargo_id_str or not str(cargo_id_str).isdigit(): return 
    cargo = guild.get_role(int(cargo_id_str))
    if not cargo: return
    for membro in cargo.members:
        if not membro.bot:
            try:
                await membro.send(texto_dm)
                await asyncio.sleep(1.5)  
            except Exception: pass

# --- INTELIGÊNCIA DE DATAS E PAINEL (AGORA MULTI-SERVIDOR) ---
def info_alvo_guerra(guild_id):
    guild_id_str = str(guild_id)
    agora = datetime.now(BR_TIMEZONE)
    
    # Busca a hora apenas para este servidor
    configs_servidor = CACHE_CONFIG.get(guild_id_str, {})
    hora_abre_str = configs_servidor.get("Horario_Abre", "22:10")
    
    try:
        ha, ma = map(int, hora_abre_str.split(":"))
    except:
        ha, ma = 22, 10
        
    if agora.hour < ha or (agora.hour == ha and agora.minute < ma):
        data_alvo = agora
    else:
        data_alvo = agora + timedelta(days=1)
        
    dia_nome = DIAS_DA_SEMANA_PT[data_alvo.weekday()]
    data_formatada = data_alvo.strftime("%d/%m")
    return data_alvo, dia_nome, data_formatada

def gerar_texto_painel(guild):
    guild_id_str = str(guild.id)
    iniciar_memoria_servidor(guild_id_str)
    
    data_alvo, dia_nome, data_formatada = info_alvo_guerra(guild_id_str)

    # Abre a "gaveta" de vagas específica deste servidor
    limites = RUNTIME[guild_id_str]["limites_atuais"]
    total_vagas = sum([int(v) for v in limites.values() if str(v).isdigit()])
    
    if total_vagas >= 40: titulo_evento = "NODE WAR T2"
    elif total_vagas > 0 and total_vagas <= 35: titulo_evento = "NODE WAR T1"
    else: titulo_evento = "NODE WAR"

    embed = discord.Embed(
        title=f"📅 {titulo_evento} - {dia_nome} {data_formatada}",
        description="Verifique os requisitos de vagas e clique no botão correspondente para se inscrever.",
        color=discord.Color.from_rgb(47, 49, 54)
    )

    categorias_visiveis = 0
    preset_atual_nome = RUNTIME[guild_id_str]["preset_ativo"]
    travas_dict = {}
    
    # Confere as classes no Preset deste servidor
    presets_servidor = CACHE_PRESETS.get(guild_id_str, {})
    if preset_atual_nome and preset_atual_nome in presets_servidor:
        for item in presets_servidor[preset_atual_nome]:
            travas_dict[item["classe"].lower()] = item["travas"]

    for cat, max_vagas in limites.items():
        max_vagas = int(max_vagas)
        if max_vagas <= 0: continue

        categorias_visiveis += 1
        inscritos = presencas_ativas[guild_id_str].get(cat, [])
        vagas_texto = ", ".join([f"<@{uid}>" for uid in inscritos]) if inscritos else "-"

        cat_lower = cat.lower().strip()
        req_cargo = "Liberado"
        trava_id = travas_dict.get(cat_lower, "")
        if trava_id: req_cargo = f"<@&{trava_id}>"

        embed.add_field(name=f"{cat} ({len(inscritos)}/{max_vagas})", value=f"🔒 **{req_cargo}**\n{vagas_texto}", inline=True)

    if categorias_visiveis == 0:
        embed.description = "⚠️ Nenhuma categoria ativa com vagas abertas para esta Guerra."

    embed.add_field(name="​", value="⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯", inline=False)
    
    # Traz a fila de espera separada por servidor
    lista_espera = wait_list_geral.get(guild_id_str, [])
    texto_wait = "\n".join([f"⏳ #{i+1} <@{j['user_id']}> ➔ **{j['funcao']}**" for i, j in enumerate(lista_espera)]) if lista_espera else "*Fila vazia*"
    embed.add_field(name="⏳ Waitlist / Fila", value=texto_wait, inline=False)
    
    return embed

# --- FUNCIONAMENTO DOS BOTÕES ---
class BotaoClasseLista(discord.ui.Button):
    def __init__(self, label, row, custom_id):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=row, custom_id=custom_id)

    async def callback(self, interaction: discord.Interaction):
        guild_id_str = str(interaction.guild.id)
        user_id = interaction.user.id
        cat_nome = self.label
        limites = RUNTIME[guild_id_str]["limites_atuais"]
        limite = int(limites.get(cat_nome, 0))

        if limite <= 0: return await interaction.response.send_message("❌ Esta categoria não possui vagas disponíveis.", ephemeral=True)

        preset_atual_nome = RUNTIME[guild_id_str]["preset_ativo"]
        trava_id = None
        presets_servidor = CACHE_PRESETS.get(guild_id_str, {})
        if preset_atual_nome and preset_atual_nome in presets_servidor:
            for item in presets_servidor[preset_atual_nome]:
                if item["classe"].lower().strip() == cat_nome.lower().strip():
                    trava_id = item["travas"]
                    break

        if trava_id and str(trava_id).isdigit():
            cargo_obj = interaction.guild.get_role(int(trava_id))
            has_role = False
            if cargo_obj and cargo_obj in interaction.user.roles: has_role = True
            else: has_role = any(str(r.id) == str(trava_id) for r in getattr(interaction.user, 'roles', []))
            if not has_role: return await interaction.response.send_message(f"❌ Acesso Negado! Você precisa do cargo <@&{trava_id}> para se inscrever.", ephemeral=True)

        if cat_nome not in presencas_ativas[guild_id_str]: presencas_ativas[guild_id_str][cat_nome] = []
        if user_id in presencas_ativas[guild_id_str][cat_nome]: return await interaction.response.send_message("⚠️ Você já está cadastrado nesta função.", ephemeral=True)

        global wait_list_geral
        for c in list(presencas_ativas[guild_id_str].keys()):
            if user_id in presencas_ativas[guild_id_str][c]:
                presencas_ativas[guild_id_str][c].remove(user_id)
                for i, j in enumerate(wait_list_geral[guild_id_str]):
                    if j["funcao"] == c:
                        promovido = wait_list_geral[guild_id_str].pop(i)
                        if c not in presencas_ativas[guild_id_str]: presencas_ativas[guild_id_str][c] = []
                        presencas_ativas[guild_id_str][c].append(promovido["user_id"])
                        asyncio.create_task(notificar_promovido_dm(interaction.client, promovido["user_id"], interaction.guild.id, c))
                        break

        wait_list_geral[guild_id_str] = [w for w in wait_list_geral[guild_id_str] if w["user_id"] != user_id]

        if len(presencas_ativas[guild_id_str][cat_nome]) < limite:
            presencas_ativas[guild_id_str][cat_nome].append(user_id)
            msg_resposta = f"✅ Você pegou a vaga de **{cat_nome}**!"
        else:
            wait_list_geral[guild_id_str].append({"user_id": user_id, "funcao": cat_nome})
            msg_resposta = f"⏳ Vagas cheias! Entrou na fila de espera."

        nova_view = GradeBotoesView(interaction.guild.id)
        await interaction.response.edit_message(embed=gerar_texto_painel(interaction.guild), view=nova_view)
        await interaction.followup.send(msg_resposta, ephemeral=True)
        asyncio.create_task(atualizar_planilha_guerra_background(interaction.guild.id))

class BotaoSairPainel(discord.ui.Button):
    def __init__(self, row):
        super().__init__(label="❌ Sair", style=discord.ButtonStyle.danger, row=row, custom_id="btn_nodewar_sair")

    async def callback(self, interaction: discord.Interaction):
        guild_id_str = str(interaction.guild.id)
        user_id = interaction.user.id
        global wait_list_geral
        removido = False

        for c in list(presencas_ativas[guild_id_str].keys()):
            if user_id in presencas_ativas[guild_id_str][c]:
                presencas_ativas[guild_id_str][c].remove(user_id)
                removido = True
                for i, j in enumerate(wait_list_geral[guild_id_str]):
                    if j["funcao"] == c:
                        promovido = wait_list_geral[guild_id_str].pop(i)
                        if c not in presencas_ativas[guild_id_str]: presencas_ativas[guild_id_str][c] = []
                        presencas_ativas[guild_id_str][c].append(promovido["user_id"])
                        asyncio.create_task(notificar_promovido_dm(interaction.client, promovido["user_id"], interaction.guild.id, c))
                        break
                break

        tamanho_antes = len(wait_list_geral[guild_id_str])
        wait_list_geral[guild_id_str] = [w for w in wait_list_geral[guild_id_str] if w["user_id"] != user_id]
        if len(wait_list_geral[guild_id_str]) < tamanho_antes: removido = True

        if removido:
            nova_view = GradeBotoesView(interaction.guild.id)
            await interaction.response.edit_message(embed=gerar_texto_painel(interaction.guild), view=nova_view)
            await interaction.followup.send("👋 Você removeu a sua inscrição.", ephemeral=True)
            asyncio.create_task(atualizar_planilha_guerra_background(interaction.guild.id))
        else:
            await interaction.response.send_message("⚠️ Você não está inscrito.", ephemeral=True)

class GradeBotoesView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        guild_id_str = str(guild_id)
        iniciar_memoria_servidor(guild_id_str)
        limites = RUNTIME[guild_id_str]["limites_atuais"]
        row_tracker = 0
        buttons_in_row = 0

        for cat, limite in limites.items():
            if int(limite) > 0:
                if buttons_in_row >= 4:
                    row_tracker += 1
                    buttons_in_row = 0

                safe_id = cat.lower().replace('/', '_').replace('-', '_').replace(' ', '_')
                self.add_item(BotaoClasseLista(label=cat, row=row_tracker, custom_id=f"btn_nw_dyn_{safe_id}"))
                buttons_in_row += 1

        row_tracker += 1
        self.add_item(BotaoSairPainel(row=row_tracker))

# --- GESTÃO AUTOMATIZADA DE PAINÉIS ---
async def ejecutar_criacao_sistema(guild, canal, nome_preset: str):
    guild_id_str = str(guild.id)
    iniciar_memoria_servidor(guild_id_str)
    
    if RUNTIME[guild_id_str]["painel_msg_id"] and RUNTIME[guild_id_str]["canal_automacao_id"]:
        try:
            c = guild.get_channel(int(RUNTIME[guild_id_str]["canal_automacao_id"]))
            if c:
                try:
                    m = await c.fetch_message(int(RUNTIME[guild_id_str]["painel_msg_id"]))
                    await m.delete()
                except: pass
                if RUNTIME[guild_id_str]["aviso_msg_id"]:
                    try:
                        m_aviso = await c.fetch_message(int(RUNTIME[guild_id_str]["aviso_msg_id"]))
                        await m_aviso.delete()
                    except: pass
        except: pass

    RUNTIME[guild_id_str]["limites_atuais"].clear()
    RUNTIME[guild_id_str]["preset_ativo"] = nome_preset
    RUNTIME[guild_id_str]["fechado_manualmente"] = False
    
    presets_servidor = CACHE_PRESETS.get(guild_id_str, {})
    if nome_preset in presets_servidor:
        for item in presets_servidor[nome_preset]:
            RUNTIME[guild_id_str]["limites_atuais"][item["classe"]] = item["limite"]
    else:
        for cat in CATEGORIAS_PADRAO_INICIAIS:
            RUNTIME[guild_id_str]["limites_atuais"][cat] = 0

    presencas_ativas[guild_id_str] = {cat: [] for cat in RUNTIME[guild_id_str]["limites_atuais"].keys()}
    wait_list_geral[guild_id_str] = []

    configs_servidor = CACHE_CONFIG.get(guild_id_str, {})
    cargo_id_str = configs_servidor.get("Cargo_Membro_ID", "")
    mencao = f"<@&{cargo_id_str}>" if cargo_id_str else "@here"
    
    view = GradeBotoesView(guild.id)
    embed_visual = gerar_texto_painel(guild)
    
    msg_painel = await canal.send(embed=embed_visual, view=view)
    msg_abertura = configs_servidor.get("Msg_Abertura", "⚔️ **PAINEL DE GUERRA ABERTO!**")
    msg_aviso = await canal.send(f"{mencao} {msg_abertura}")

    RUNTIME[guild_id_str]["painel_msg_id"] = msg_painel.id
    RUNTIME[guild_id_str]["aviso_msg_id"] = msg_aviso.id
    RUNTIME[guild_id_str]["canal_automacao_id"] = canal.id
    
    asyncio.create_task(atualizar_planilha_guerra_background(guild.id))
    asyncio.create_task(notificar_membros_dm(guild, nome_preset))

async def ejecutar_encerramento_sistema(guild, canal_fallback):
    guild_id_str = str(guild.id)
    iniciar_memoria_servidor(guild_id_str)
    
    painel_id = RUNTIME[guild_id_str]["painel_msg_id"]
    aviso_id = RUNTIME[guild_id_str]["aviso_msg_id"]
    canal_id = RUNTIME[guild_id_str]["canal_automacao_id"]
    
    canal_alvo = None
    if canal_id:
        try: canal_alvo = guild.get_channel(int(canal_id)) or await guild.fetch_channel(int(canal_id))
        except: pass
        
    if not canal_alvo: canal_alvo = canal_fallback

    try:
        planilha, _ = await obter_planilha_servidor(guild_id_str)
        if planilha:
            aba_historico = planilha.worksheet("Historico")
            data_hoje = datetime.now(BR_TIMEZONE).strftime("%d/%m/%Y")
            linhas_historico = []
            for classe, membros in presencas_ativas[guild_id_str].items():
                for uid in membros:
                    linhas_historico.append([data_hoje, str(uid), classe, "Confirmado"])
            for w in wait_list_geral[guild_id_str]:
                linhas_historico.append([data_hoje, str(w["user_id"]), w["funcao"], "Fila de Espera"])
            if linhas_historico:
                aba_historico.append_rows(linhas_historico)
    except Exception as e: print(f"⚠️ Erro ao salvar histórico: {e}")

    if painel_id:
        try:
            msg = await canal_alvo.fetch_message(int(painel_id))
            await msg.delete()
        except: pass
        
    if aviso_id:
        try:
            msg_aviso = await canal_alvo.fetch_message(int(aviso_id))
            await msg_aviso.delete()
        except: pass

    RUNTIME[guild_id_str]["painel_msg_id"] = None
    RUNTIME[guild_id_str]["aviso_msg_id"] = None
    RUNTIME[guild_id_str]["fechado_manualmente"] = True
    
    for k in presencas_ativas[guild_id_str].keys(): presencas_ativas[guild_id_str][k] = []
    wait_list_geral[guild_id_str].clear()
    
    asyncio.create_task(atualizar_planilha_guerra_background(guild.id))
    await canal_fallback.send("🛑 **A GUERRA foi encerrada! O painel foi fechado.**", delete_after=120.0)

# --- SISTEMA DE RELATÓRIO DE FREQUÊNCIA (FASE 4) ---
def quebrar_lista_em_partes(lista, separador, limite=900):
    partes = []
    atual = ""
    for item in lista:
        if len(atual) + len(item) + len(separador) > limite:
            partes.append(atual)
            atual = item
        else:
            atual = atual + separador + item if atual else item
    if atual:
        partes.append(atual)
    return partes

async def gerar_relatorio_semanal(guild):
    guild_id_str = str(guild.id)
    configs_servidor = CACHE_CONFIG.get(guild_id_str, {})
    canal_id = configs_servidor.get("Canal_Relatorio_ID")
    
    if not canal_id or not str(canal_id).isdigit(): return False, "Canal de destino não configurado."
    
    canal = guild.get_channel(int(canal_id))
    if not canal: return False, "Canal de destino não encontrado no servidor."

    try:
        planilha, _ = await obter_planilha_servidor(guild_id_str)
        if not planilha: return False, "Planilha da guilda não encontrada."
        
        aba_historico = planilha.worksheet("Historico")
        dados = aba_historico.get_all_values()
        
        cargo_id_str = configs_servidor.get("Cargo_Membro_ID", "")
        membros_oficiais = set()
        if cargo_id_str and str(cargo_id_str).isdigit():
            cargo = guild.get_role(int(cargo_id_str))
            if cargo: membros_oficiais = {str(m.id) for m in cargo.members if not m.bot}

        frequencia = {}
        if len(dados) > 1:
            for linha in dados[1:]:
                if len(linha) >= 4:
                    uid = linha[1]
                    status = linha[3]
                    if status == "Confirmado" and uid.isdigit():
                        frequencia[uid] = frequencia.get(uid, 0) + 1
        
        if not frequencia and not membros_oficiais: return False, "Nenhum membro ou histórico encontrado."

        ranking = sorted(frequencia.items(), key=lambda x: x[1], reverse=True)
        linhas_ranking = []
        if ranking:
            for i, (uid, freq) in enumerate(ranking):
                medalha = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else "🔹"
                linhas_ranking.append(f"{medalha} <@{uid}> ➔ **{freq}** presenças")
        else:
            linhas_ranking.append("*Ninguém participou de guerras nesta semana.*")

        membros_presentes = set(frequencia.keys())
        membros_zerados = membros_oficiais - membros_presentes
        linhas_ausentes = []
        if membros_zerados:
            linhas_ausentes = [f"<@{uid}>" for uid in membros_zerados]
        else:
            linhas_ausentes = ["🎉 *Todos os membros participaram de pelo menos uma guerra!*"]

        blocos_ranking = quebrar_lista_em_partes(linhas_ranking, "\n")
        blocos_ausentes = quebrar_lista_em_partes(linhas_ausentes, ", ")

        embeds = []
        embed_atual = discord.Embed(
            title="📊 RELATÓRIO SEMANAL DE NODE WARS",
            description="Confira o engajamento e a presença dos membros nas guerras desta semana!",
            color=discord.Color.brand_green()
        )
        
        for i, bloco in enumerate(blocos_ranking):
            titulo = "🏆 Ranking de Participação" if i == 0 else "🏆 Ranking (Continuação)"
            embed_atual.add_field(name=titulo, value=bloco, inline=False)
            if len(embed_atual.fields) >= 3:
                embeds.append(embed_atual)
                embed_atual = discord.Embed(color=discord.Color.brand_green())

        for i, bloco in enumerate(blocos_ausentes):
            titulo = "👻 Ausentes (0 Presenças)" if i == 0 else "👻 Ausentes (Continuação)"
            embed_atual.add_field(name=titulo, value=bloco, inline=False)
            if len(embed_atual.fields) >= 3:
                embeds.append(embed_atual)
                embed_atual = discord.Embed(color=discord.Color.brand_green())

        if len(embed_atual.fields) > 0 and embed_atual not in embeds:
            embeds.append(embed_atual)

        embeds[-1].set_footer(text="Database Solutions • Histórico resetado para a próxima semana.")
        
        await canal.send(embeds=embeds[:10])
        aba_historico.clear()
        aba_historico.append_row(["Data", "ID_Discord", "Classe", "Status"])
        
        return True, "Relatório enviado e cofre resetado!"
    except Exception as e: return False, f"Erro ao gerar: {e}"

# --- FORMULÁRIO E PAINEL STAFF (/help) ---
class ModalAbrirPainel(discord.ui.Modal, title="Abrir Painel de Guerra"):
    preset_nome = discord.ui.TextInput(label="Qual Preset deseja abrir?", placeholder="Ex: T1-25", required=True, max_length=30)
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        preset = self.preset_nome.value.strip()
        guild_id_str = str(interaction.guild.id)
        configs = CACHE_CONFIG.get(guild_id_str, {})
        canal_id = configs.get("Canal_Painel_ID")
        canal_alvo = interaction.guild.get_channel(int(canal_id)) if canal_id and str(canal_id).isdigit() else interaction.channel
        if not canal_alvo: return await interaction.followup.send("❌ Canal oficial não configurado.", ephemeral=True)
        await ejecutar_criacao_sistema(interaction.guild, canal_alvo, preset)
        await interaction.followup.send(f"✅ Painel do preset **[{preset}]** gerado com sucesso!", ephemeral=True)
        await enviar_log_staff(interaction.guild, f"{interaction.user.mention} abriu manualmente o preset **{preset}**.")

class ViewPainelStaff(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔄 Sincronizar", style=discord.ButtonStyle.primary, custom_id="btn_staff_sync", row=0)
    async def btn_sync(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        sucesso, msg = await sincronizar_planilha(interaction.guild.id)
        await interaction.followup.send(msg, ephemeral=True)
        await enviar_log_staff(interaction.guild, f"{interaction.user.mention} sincronizou o Banco de Dados.")

    @discord.ui.button(label="▶️ Abrir Painel", style=discord.ButtonStyle.success, custom_id="btn_staff_abrir", row=0)
    async def btn_abrir(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ModalAbrirPainel())

    @discord.ui.button(label="🛑 Fechar Painel", style=discord.ButtonStyle.danger, custom_id="btn_staff_fechar", row=0)
    async def btn_fechar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        guild_id_str = str(interaction.guild.id)
        configs = CACHE_CONFIG.get(guild_id_str, {})
        canal_id = configs.get("Canal_Painel_ID")
        canal_alvo = interaction.guild.get_channel(int(canal_id)) if canal_id and str(canal_id).isdigit() else interaction.channel
        if not canal_alvo: return await interaction.followup.send("❌ Canal não encontrado.", ephemeral=True)
        await ejecutar_encerramento_sistema(interaction.guild, canal_alvo)
        await interaction.followup.send("✅ Painel encerrado com sucesso!", ephemeral=True)
        await enviar_log_staff(interaction.guild, f"{interaction.user.mention} encerrou o painel.")

    @discord.ui.button(label="📊 Disparar Relatório", style=discord.ButtonStyle.secondary, custom_id="btn_staff_rel", row=1)
    async def btn_relatorio(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        sucesso, msg = await gerar_relatorio_semanal(interaction.guild)
        await interaction.followup.send(f"Status: {msg}", ephemeral=True)
        await enviar_log_staff(interaction.guild, f"{interaction.user.mention} gerou o Relatório Semanal.")

    @discord.ui.button(label="⚙️ Ligar/Desligar Motor", style=discord.ButtonStyle.secondary, custom_id="btn_staff_motor", row=1)
    async def btn_motor(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        guild_id_str = str(interaction.guild.id)
        status_atual = CACHE_CONFIG.get(guild_id_str, {}).get("Automacao_Ativa", "1")
        novo_status = "0" if str(status_atual) == "1" else "1"
        texto_status = "🟢 LIGADO" if novo_status == "1" else "🛑 DESLIGADO"
        
        try:
            planilha, _ = await obter_planilha_servidor(guild_id_str)
            if not planilha: return await interaction.followup.send("❌ Planilha não encontrada.", ephemeral=True)
            aba = planilha.worksheet("Config_Geral")
            dados = aba.get_all_values()
            linha_alvo = next((i + 1 for i, linha in enumerate(dados) if i > 0 and len(linha) > 0 and linha[0].strip() == "Automacao_Ativa"), None)
            if linha_alvo: aba.update_acell(f"B{linha_alvo}", novo_status)
            else: aba.append_row(["Automacao_Ativa", novo_status])
                
            await sincronizar_planilha(interaction.guild.id)
            await interaction.followup.send(f"✅ O Motor Automático foi **{texto_status}**!", ephemeral=True)
            await enviar_log_staff(interaction.guild, f"{interaction.user.mention} alterou o Motor para {texto_status}.")
        except Exception as e: await interaction.followup.send(f"❌ Erro: {e}", ephemeral=True)

@bot.tree.command(name="help", description="🛠️ Abre o Painel Supremo de Auditoria e Controle da Staff")
async def help_cmd(interaction: discord.Interaction):
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao: return await interaction.response.send_message("❌ Acesso Negado!", ephemeral=True)
    
    guild_id_str = str(interaction.guild.id)
    configs = CACHE_CONFIG.get(guild_id_str, {})
    cronos = CACHE_CRONOGRAMA.get(guild_id_str, {})
    
    c_membro = f"<@&{configs.get('Cargo_Membro_ID')}>" if configs.get('Cargo_Membro_ID') else "❌ `Não configurado`"
    c_auto = f"<#{configs.get('Canal_Painel_ID')}>" if configs.get('Canal_Painel_ID') else "❌ `Não configurado`"
    c_logs = f"<#{configs.get('Canal_Logs_ID')}>" if configs.get('Canal_Logs_ID') else "❌ `Não configurado`"
    h_abre = configs.get("Horario_Abre", "22:10")
    h_fecha = configs.get("Horario_Fecha", "22:05")
    
    c_relatorio = f"<#{configs.get('Canal_Relatorio_ID')}>" if configs.get('Canal_Relatorio_ID') else "❌ `Não configurado`"
    dia_rel = configs.get("Dia_Relatorio", "❌ `Não configurado`")
    hora_rel = configs.get("Horario_Relatorio", "❌ `Não configurado`")
    
    status_motor = configs.get("Automacao_Ativa", "1")
    status_auto = "🟢 `ATIVO`" if str(status_motor) == "1" else "🛑 `PAUSADO`"

    embed_auditoria = discord.Embed(title="👑 PAINEL SUPREMO DE AUDITORIA", color=discord.Color.from_rgb(255, 215, 0))
    embed_auditoria.add_field(name="🌐 Infraestrutura Core", value=f"🔹 **Motor:** {status_auto}\n🔹 **Membro:** {c_membro}\n🔹 **Painel:** {c_auto}\n🔹 **Logs:** {c_logs}", inline=False)
    embed_auditoria.add_field(name="⏳ Loops de Guerra", value=f"⏰ **Abre:** `{h_abre}`\n⏰ **Fecha:** `{h_fecha}`", inline=False)
    embed_auditoria.add_field(name="📊 Disparo do Relatório", value=f"🔹 **Destino:** {c_relatorio}\n🔹 **Agendamento:** Todo(a) `{dia_rel}` às `{hora_rel}`", inline=False)
    
    msg_abertura = configs.get("Msg_Abertura", "⚔️ PAINEL ABERTO!")
    msg_dm = configs.get("Msg_DM_Abertura", "O Painel para a GUERRA já está aberto!")
    msg_promocao = configs.get("Msg_Promocao", "Você foi promovido da fila!")
    embed_auditoria.add_field(name="💬 Mensagens Atuais", value=f"**Chat:**\n*{msg_abertura}*\n\n**DM Abertura:**\n*{msg_dm}*\n\n**DM Promoção:**\n*{msg_promocao}*", inline=False)

    embed_crono = discord.Embed(title="🗓️ Cronograma Semanal", color=discord.Color.blue())
    crono_texto = ""
    for i in range(7):
        dia = DIAS_DA_SEMANA_PT[i]
        preset_dia = cronos.get(dia, "")
        if not preset_dia or str(preset_dia).lower() in ["none", "folga", "descanso", ""]:
            crono_texto += f"**{dia}:** 💤 `Descanso`\n"
        else: crono_texto += f"**{dia}:** ⚔️ Preset `[{preset_dia}]`\n"
    embed_crono.add_field(name="Escala", value=crono_texto, inline=False)

    # 👇 A TERCEIRA EMBED COM OS COMANDOS
    embed_comandos = discord.Embed(title="💻 Guia de Comandos Slash", color=discord.Color.dark_grey())
    embed_comandos.add_field(name="⚙️ Configuração (Planilha)", value="`/config_geral` - Altera IDs, canais e horários\n`/config_mensagens` - Altera mensagens automáticas\n`/cronograma_configurar` - Define a agenda da semana", inline=False)
    embed_comandos.add_field(name="⚔️ Presets e Vagas", value="`/preset_configurar` - Cria ou atualiza uma vaga/classe\n`/preset_remover` - Remove uma classe de um preset", inline=False)
    embed_comandos.add_field(name="🛡️ Moderação e Controle", value="`/forcar_presenca` - Adiciona/remove membro manualmente no painel\n`/sync` - Força atualização imediata com o Google Sheets", inline=False)

    view = ViewPainelStaff()
    # 👇 NOTA: embed_comandos adicionada à lista abaixo!
    await interaction.response.send_message(embeds=[embed_auditoria, embed_crono, embed_comandos], view=view, ephemeral=True)

# --- COMANDOS PARA A CONFIGURAÇÃO REMOTA ---
@bot.tree.command(name="config_geral", description="⚙️ Altera configurações estruturais.")
@discord.app_commands.describe(configuracao="O que deseja alterar?", valor="O ID ou Horário (ex: 22:00)")
@discord.app_commands.choices(configuracao=[
    discord.app_commands.Choice(name="Canal Oficial do Painel", value="Canal_Painel_ID"),
    discord.app_commands.Choice(name="Canal de Logs", value="Canal_Logs_ID"),
    discord.app_commands.Choice(name="Cargo Oficial de Membro", value="Cargo_Membro_ID"),
    discord.app_commands.Choice(name="Horário de Abertura", value="Horario_Abre"),
    discord.app_commands.Choice(name="Horário de Fechamento", value="Horario_Fecha"),
    discord.app_commands.Choice(name="Motor Automático (1=LIGADO, 0=DESLIGADO)", value="Automacao_Ativa"),
    discord.app_commands.Choice(name="Canal do Relatório", value="Canal_Relatorio_ID"),
    discord.app_commands.Choice(name="Dia do Relatório", value="Dia_Relatorio"),
    discord.app_commands.Choice(name="Horário do Relatório", value="Horario_Relatorio")
])
async def config_geral_cmd(interaction: discord.Interaction, configuracao: discord.app_commands.Choice[str], valor: str):
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao: return await interaction.response.send_message("❌ Acesso Negado!", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    try:
        guild_id_str = str(interaction.guild.id)
        planilha, _ = await obter_planilha_servidor(guild_id_str)
        if not planilha: return await interaction.followup.send("❌ Planilha não encontrada.", ephemeral=True)
        aba = planilha.worksheet("Config_Geral")
        dados = aba.get_all_values()
        chave = configuracao.value
        linha_alvo = next((i + 1 for i, linha in enumerate(dados) if i > 0 and len(linha) > 0 and linha[0].strip() == chave), None)
        if linha_alvo: aba.update_acell(f"B{linha_alvo}", valor)
        else: aba.append_row([chave, valor])
        await sincronizar_planilha(interaction.guild.id)
        await interaction.followup.send(f"✅ Atualizado para `{valor}`!", ephemeral=True)
        await enviar_log_staff(interaction.guild, f"{interaction.user.mention} alterou **{configuracao.name}** para `{valor}`.")
    except Exception as e: await interaction.followup.send(f"❌ Erro: {e}", ephemeral=True)

@bot.tree.command(name="config_mensagens", description="💬 Altera as mensagens automáticas.")
@discord.app_commands.choices(tipo=[
    discord.app_commands.Choice(name="Aviso de Abertura (Chat)", value="Msg_Abertura"),
    discord.app_commands.Choice(name="Aviso de Abertura (DM Privada)", value="Msg_DM_Abertura"),
    discord.app_commands.Choice(name="Promoção da Fila (DM)", value="Msg_Promocao")
])
async def config_mensagens_cmd(interaction: discord.Interaction, tipo: discord.app_commands.Choice[str], mensagem: str):
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao: return await interaction.response.send_message("❌ Acesso Negado!", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    try:
        guild_id_str = str(interaction.guild.id)
        planilha, _ = await obter_planilha_servidor(guild_id_str)
        if not planilha: return await interaction.followup.send("❌ Planilha não encontrada.", ephemeral=True)
        aba = planilha.worksheet("Config_Geral")
        dados = aba.get_all_values()
        chave = tipo.value
        linha_alvo = next((i + 1 for i, linha in enumerate(dados) if i > 0 and len(linha) > 0 and linha[0].strip() == chave), None)
        if linha_alvo: aba.update_acell(f"B{linha_alvo}", mensagem)
        else: aba.append_row([chave, mensagem])
        await sincronizar_planilha(interaction.guild.id)
        await interaction.followup.send(f"✅ Nova **{tipo.name}** registrada!", ephemeral=True)
    except Exception as e: await interaction.followup.send(f"❌ Erro: {e}", ephemeral=True)

@bot.tree.command(name="cronograma_configurar", description="🗓️ Define qual preset será aberto.")
@discord.app_commands.choices(dia=[
    discord.app_commands.Choice(name="Segunda-feira", value="Segunda"), discord.app_commands.Choice(name="Terça-feira", value="Terca"),
    discord.app_commands.Choice(name="Quarta-feira", value="Quarta"), discord.app_commands.Choice(name="Quinta-feira", value="Quinta"),
    discord.app_commands.Choice(name="Sexta-feira", value="Sexta"), discord.app_commands.Choice(name="Sábado", value="Sabado"),
    discord.app_commands.Choice(name="Domingo", value="Domingo")
])
async def cronograma_configurar_cmd(interaction: discord.Interaction, dia: discord.app_commands.Choice[str], preset: str):
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao: return await interaction.response.send_message("❌ Acesso Negado!", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    try:
        guild_id_str = str(interaction.guild.id)
        planilha, _ = await obter_planilha_servidor(guild_id_str)
        if not planilha: return await interaction.followup.send("❌ Planilha não encontrada.", ephemeral=True)
        aba = planilha.worksheet("Cronograma")
        dados = aba.get_all_values()
        chave = dia.value
        linha_alvo = next((i + 1 for i, linha in enumerate(dados) if i > 0 and len(linha) > 0 and linha[0].strip() == chave), None)
        if linha_alvo: aba.update_acell(f"B{linha_alvo}", preset)
        else: aba.append_row([chave, preset])
        await sincronizar_planilha(interaction.guild.id)
        await interaction.followup.send(f"🗓️ O dia **{dia.name}** roda o preset **{preset}**.", ephemeral=True)
    except Exception as e: await interaction.followup.send(f"❌ Erro: {e}", ephemeral=True)

@bot.tree.command(name="preset_configurar", description="⚙️ Cria ou atualiza uma vaga de Preset.")
async def preset_configurar(interaction: discord.Interaction, preset_nome: str, classe: str, vagas: int, cargo_trava: discord.Role = None):
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao: return await interaction.response.send_message("❌ Acesso Negado!", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    try:
        guild_id_str = str(interaction.guild.id)
        planilha, _ = await obter_planilha_servidor(guild_id_str)
        if not planilha: return await interaction.followup.send("❌ Planilha não encontrada.", ephemeral=True)
        aba_presets = planilha.worksheet("Setup_Presets")
        dados = aba_presets.get_all_values()
        preset_upper = preset_nome.upper().strip()
        classe_upper = classe.upper().strip()
        trava_id = str(cargo_trava.id) if cargo_trava else ""
        linha_encontrada = next((i + 1 for i, linha in enumerate(dados) if i > 0 and len(linha) >= 2 and linha[0].upper().strip() == preset_upper and linha[1].upper().strip() == classe_upper), None)
        if linha_encontrada:
            aba_presets.update(f"C{linha_encontrada}:D{linha_encontrada}", [[vagas, trava_id]])
            msg = f"🔄 Vaga de **{classe_upper}** ATUALIZADA."
        else:
            aba_presets.append_row([preset_upper, classe_upper, vagas, trava_id])
            msg = f"✅ Nova vaga de **{classe_upper}** CRIADA."
        await sincronizar_planilha(interaction.guild.id)
        await interaction.followup.send(msg, ephemeral=True)
    except Exception as e: await interaction.followup.send(f"❌ Erro ao salvar: {e}", ephemeral=True)

@bot.tree.command(name="preset_remover", description="🗑️ Remove uma classe de um Preset.")
async def preset_remover(interaction: discord.Interaction, preset_nome: str, classe: str):
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao: return await interaction.response.send_message("❌ Acesso Negado!", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    try:
        guild_id_str = str(interaction.guild.id)
        planilha, _ = await obter_planilha_servidor(guild_id_str)
        if not planilha: return await interaction.followup.send("❌ Planilha não encontrada.", ephemeral=True)
        aba_presets = planilha.worksheet("Setup_Presets")
        dados = aba_presets.get_all_values()
        preset_upper = preset_nome.upper().strip()
        classe_upper = classe.upper().strip()
        linha_deletar = next((i + 1 for i, linha in enumerate(dados) if i > 0 and len(linha) >= 2 and linha[0].upper().strip() == preset_upper and linha[1].upper().strip() == classe_upper), None)
        if linha_deletar:
            aba_presets.delete_rows(linha_deletar)
            await sincronizar_planilha(interaction.guild.id)
            await interaction.followup.send(f"🗑️ A classe **{classe_upper}** foi deletada.", ephemeral=True)
        else: await interaction.followup.send(f"⚠️ A classe **{classe_upper}** não foi encontrada.", ephemeral=True)
    except Exception as e: await interaction.followup.send(f"❌ Erro ao aceder ao banco: {e}", ephemeral=True)

@bot.tree.command(name="forcar_presenca", description="👥 Adiciona ou remove membro manualmente.")
@discord.app_commands.choices(acao=[discord.app_commands.Choice(name="Adicionar", value="adicionar"), discord.app_commands.Choice(name="Remover", value="remover")])
async def forcar_presenca_cmd(interaction: discord.Interaction, membro: discord.Member, acao: discord.app_commands.Choice[str], classe: str = None):
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao: return await interaction.response.send_message("❌ Acesso Negado!", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    
    guild = interaction.guild
    guild_id_str = str(guild.id)
    user_id = membro.id
    
    for c in list(presencas_ativas[guild_id_str].keys()):
        if user_id in presencas_ativas[guild_id_str][c]:
            presencas_ativas[guild_id_str][c].remove(user_id)
            for i, j in enumerate(wait_list_geral[guild_id_str]):
                if j["funcao"] == c:
                    promovido = wait_list_geral[guild_id_str].pop(i)
                    if c not in presencas_ativas[guild_id_str]: presencas_ativas[guild_id_str][c] = []
                    presencas_ativas[guild_id_str][c].append(promovido["user_id"])
                    asyncio.create_task(notificar_promovido_dm(interaction.client, promovido["user_id"], guild.id, c))
                    break
            break
            
    wait_list_geral[guild_id_str] = [w for w in wait_list_geral[guild_id_str] if w["user_id"] != user_id]
    
    if acao.value == "remover": msg_final = f"✅ O membro {membro.mention} foi removido."
    else:
        if not classe: return await interaction.followup.send("❌ Especifique a classe.", ephemeral=True)
        cat_alvo = None
        busca = classe.lower().strip()
        for k in presencas_ativas[guild_id_str].keys():
            if busca in k.lower():
                cat_alvo = k
                break
        if not cat_alvo: return await interaction.followup.send(f"❌ A classe `{classe}` não está ativa.", ephemeral=True)
        
        limite = int(RUNTIME[guild_id_str]["limites_atuais"].get(cat_alvo, 0))
        if len(presencas_ativas[guild_id_str][cat_alvo]) < limite:
            presencas_ativas[guild_id_str][cat_alvo].append(user_id)
            msg_final = f"✅ O membro {membro.mention} foi colocado em **{cat_alvo}**!"
        else:
            wait_list_geral[guild_id_str].append({"user_id": user_id, "funcao": cat_alvo})
            msg_final = f"⏳ Vagas cheias para **{cat_alvo}**! {membro.mention} foi para a Fila."

    if RUNTIME[guild_id_str]["painel_msg_id"] and RUNTIME[guild_id_str]["canal_automacao_id"]:
        try:
            canal = guild.get_channel(int(RUNTIME[guild_id_str]["canal_automacao_id"]))
            if canal:
                msg_painel = await canal.fetch_message(int(RUNTIME[guild_id_str]["painel_msg_id"]))
                await msg_painel.edit(embed=gerar_texto_painel(guild), view=GradeBotoesView(guild.id))
        except Exception: pass

    asyncio.create_task(atualizar_planilha_guerra_background(guild.id))
    await interaction.followup.send(msg_final, ephemeral=True)
    await enviar_log_staff(guild, f"{interaction.user.mention} forçou {acao.name} para {membro.mention} em `{classe or 'N/A'}`.")

@bot.tree.command(name="sync", description="🔄 Força o bot a baixar as novidades do Google.")
async def sync_cmd(interaction: discord.Interaction):
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao: return await interaction.response.send_message("❌ Acesso Negado!", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    sucesso, mensagem = await sincronizar_planilha(interaction.guild.id)
    await interaction.followup.send(mensagem)

async def recuperar_estado_guerra(guild):
    guild_id_str = str(guild.id)
    iniciar_memoria_servidor(guild_id_str)
    try:
        configs = CACHE_CONFIG.get(guild_id_str, {})
        canal_id = configs.get("Canal_Painel_ID")
        if not canal_id: return
        canal = guild.get_channel(int(canal_id))
        if not canal: return

        painel_msg = None
        aviso_msg = None
        async for msg in canal.history(limit=20):
            if msg.author == bot.user:
                if msg.embeds and msg.embeds[0].title and "📅" in msg.embeds[0].title:
                    if not painel_msg: painel_msg = msg
                elif "@" in msg.content or "PAINEL DE GUERRA ABERTO" in msg.content:
                    if not aviso_msg: aviso_msg = msg

        if painel_msg:
            RUNTIME[guild_id_str]["painel_msg_id"] = painel_msg.id
            RUNTIME[guild_id_str]["canal_automacao_id"] = canal.id
            if aviso_msg: RUNTIME[guild_id_str]["aviso_msg_id"] = aviso_msg.id

            _, dia_nome, _ = info_alvo_guerra(guild_id_str)
            cronos = CACHE_CRONOGRAMA.get(guild_id_str, {})
            preset_recuperado = cronos.get(dia_nome, "")

            RUNTIME[guild_id_str]["preset_ativo"] = preset_recuperado
            RUNTIME[guild_id_str]["limites_atuais"].clear()
            
            presets_servidor = CACHE_PRESETS.get(guild_id_str, {})
            if preset_recuperado in presets_servidor:
                for item in presets_servidor[preset_recuperado]:
                    RUNTIME[guild_id_str]["limites_atuais"][item["classe"]] = item["limite"]
            else:
                for cat in CATEGORIAS_PADRAO_INICIAIS:
                    RUNTIME[guild_id_str]["limites_atuais"][cat] = 0

            presencas_ativas[guild_id_str].clear()
            wait_list_geral[guild_id_str].clear()
            for cat in RUNTIME[guild_id_str]["limites_atuais"].keys():
                presencas_ativas[guild_id_str][cat] = []

            planilha, _ = await obter_planilha_servidor(guild_id_str)
            if not planilha: return
            aba_guerra = planilha.worksheet("Guerra_Atual")
            dados_guerra = aba_guerra.get_all_values()
            
            for linha in dados_guerra[1:]:
                if len(linha) >= 4:
                    uid, _, classe, status = linha[0], linha[1], linha[2], linha[3]
                    if uid.isdigit():
                        if status == "Confirmado" and classe in presencas_ativas[guild_id_str]:
                            presencas_ativas[guild_id_str][classe].append(int(uid))
                        elif status == "Fila de Espera":
                            wait_list_geral[guild_id_str].append({"user_id": int(uid), "funcao": classe})
    except Exception: pass

# --- AUTOMATIZAÇÃO DE HORÁRIO DA GUERRA ---
@tasks.loop(minutes=1)
async def verificador_horarios_loop():
    for guild in bot.guilds:
        guild_id_str = str(guild.id)
        configs = CACHE_CONFIG.get(guild_id_str)
        cronos = CACHE_CRONOGRAMA.get(guild_id_str)
        
        if not configs or not cronos: continue
        status_motor = configs.get("Automacao_Ativa", "1")
        if str(status_motor) != "1": continue 
        
        agora = datetime.now(BR_TIMEZONE)
        _, dia_alvo_nome, _ = info_alvo_guerra(guild_id_str)
        
        hora_abre_str = configs.get("Horario_Abre")
        hora_fecha_str = configs.get("Horario_Fecha")
        canal_id = configs.get("Canal_Painel_ID")
        
        if not canal_id: continue
        canal = guild.get_channel(int(canal_id))
        if not canal: continue

        preset_alvo = cronos.get(dia_alvo_nome, "")
        eh_dia_de_guerra = preset_alvo and str(preset_alvo).lower() not in ["", "none", "folga", "descanso"]

        try:
            ha_h, ha_m = map(int, hora_abre_str.split(":"))
            hf_h, hf_m = map(int, hora_fecha_str.split(":"))
            minutos_atual = agora.hour * 60 + agora.minute
            minutos_abre = ha_h * 60 + ha_m
            minutos_fecha = hf_h * 60 + hf_m
        except Exception:
            continue

        if minutos_abre < minutos_fecha:
            dentro_da_janela = minutos_abre <= minutos_atual < minutos_fecha
        else:
            dentro_da_janela = minutos_atual >= minutos_abre or minutos_atual < minutos_fecha

        if eh_dia_de_guerra and dentro_da_janela:
            if RUNTIME[guild_id_str]["painel_msg_id"] is None and not RUNTIME[guild_id_str].get("fechado_manualmente", False):
                asyncio.create_task(ejecutar_criacao_sistema(guild, canal, preset_alvo))
                await enviar_log_staff(guild, f"⏰ O motor detectou a janela ativa e ABRIU o painel [{preset_alvo}].")
        else:
            RUNTIME[guild_id_str]["fechado_manualmente"] = False 
            if RUNTIME[guild_id_str]["painel_msg_id"] is not None:
                asyncio.create_task(ejecutar_encerramento_sistema(guild, canal))
                await enviar_log_staff(guild, f"⏰ O motor detectou o fim do horário e FECHOU o painel.")

# --- AUTOMATIZAÇÃO DO RELATÓRIO SEMANAL ---
@tasks.loop(minutes=1)
async def verificador_relatorio_loop():
    for guild in bot.guilds:
        guild_id_str = str(guild.id)
        configs = CACHE_CONFIG.get(guild_id_str)
        if not configs: continue
        
        dia_alvo = configs.get("Dia_Relatorio", "").lower().strip()
        hora_alvo = configs.get("Horario_Relatorio", "").strip()
        
        if not dia_alvo or not hora_alvo or "não configurado" in dia_alvo: continue
        
        agora = datetime.now(BR_TIMEZONE)
        dia_hoje = DIAS_DA_SEMANA_PT[agora.weekday()].lower()
        hora_atual = f"{agora.hour:02d}:{agora.minute:02d}"
        
        if dia_hoje == dia_alvo and hora_atual == hora_alvo:
            if not RUNTIME[guild_id_str].get("relatorio_enviado_hoje"):
                RUNTIME[guild_id_str]["relatorio_enviado_hoje"] = True
                sucesso, msg = await gerar_relatorio_semanal(guild)
                await enviar_log_staff(guild, f"📊 Relatório Automático disparado: {msg}")
        else:
            RUNTIME[guild_id_str]["relatorio_enviado_hoje"] = False

@bot.event
async def on_ready():
    print(f"✅ Bot conectado como {bot.user}")
    
    # Sincroniza todas as guildas onde o bot está ativo
    for guild in bot.guilds:
        print(f"🔄 A Sincronizar servidor: {guild.name}")
        await sincronizar_planilha(guild.id)
        await recuperar_estado_guerra(guild)
        
    try:
        synced = await bot.tree.sync()
        print(f"🔄 {len(synced)} comandos globais oficiais sincronizados.")
    except Exception as e:
        print(f"⚠️ Erro ao sincronizar comandos: {e}")
        
    if not verificador_horarios_loop.is_running(): 
        verificador_horarios_loop.start()
    
    if not verificador_relatorio_loop.is_running():
        verificador_relatorio_loop.start()

keep_alive()
bot.run(os.environ.get("DISCORD_TOKEN"))
