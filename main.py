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
CACHE_CONFIG = {}
CACHE_CRONOGRAMA = {}
CACHE_PRESETS = {}

# --- ⚡ MEMÓRIA DE EXECUÇÃO RÁPIDA (RAM) ---
RUNTIME = {
    "painel_msg_id": None,
    "aviso_msg_id": None,
    "canal_automacao_id": None,
    "limites_atuais": {},  
    "preset_ativo": None
}

presencas_ativas = {}
wait_list_geral = []

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

gc = None
planilha = None

# --- CONEXÃO E SINCRONIZAÇÃO COM O GOOGLE SHEETS ---
async def sincronizar_planilha():
    global CACHE_CONFIG, CACHE_CRONOGRAMA, CACHE_PRESETS, gc, planilha
    try:
        google_creds_json = os.environ.get("GOOGLE_CREDENTIALS")
        creds_dict = json.loads(google_creds_json)
        gc = gspread.service_account_from_dict(creds_dict)
        planilha = gc.open("DB-Teste-G59") 
        
        # 1. Configurações Gerais
        aba_config = planilha.worksheet("Config_Geral")
        dados_config = aba_config.get_all_values()
        CACHE_CONFIG.clear()
        for linha in dados_config[1:]:
            if len(linha) >= 2 and linha[0].strip() != "":
                CACHE_CONFIG[linha[0].strip()] = linha[1].strip()

        # 2. Cronograma Semanal
        aba_crono = planilha.worksheet("Cronograma")
        dados_crono = aba_crono.get_all_values()
        CACHE_CRONOGRAMA.clear()
        for linha in dados_crono[1:]:
            if len(linha) >= 2 and linha[0].strip() != "":
                CACHE_CRONOGRAMA[linha[0].strip()] = linha[1].strip()

        # 3. Presets de Guerra e Travas
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
                
        if "Canal_Painel_ID" in CACHE_CONFIG:
            RUNTIME["canal_automacao_id"] = CACHE_CONFIG["Canal_Painel_ID"]
            
        return True, "✅ Sincronização concluída! O bot gravou a planilha na memória."
    except Exception as e:
        print(f"❌ Erro na Sincronização: {e}")
        return False, f"❌ Erro ao ler a planilha: {e}"

# --- ESCRITA EM TEMPO REAL NA PLANILHA (Aba Guerra_Atual) ---
async def atualizar_planilha_guerra_background():
    try:
        if not planilha: return
        aba_guerra = planilha.worksheet("Guerra_Atual")
        linhas = [["ID_Discord", "Nickname", "Classe", "Status"]]
        
        for classe, membros in presencas_ativas.items():
            for user_id in membros:
                linhas.append([str(user_id), "Membro", classe, "Confirmado"])
                
        for w in wait_list_geral:
            linhas.append([str(w["user_id"]), "Membro", w["funcao"], "Fila de Espera"])
            
        aba_guerra.clear()
        if linhas:
            aba_guerra.append_rows(linhas)
    except Exception as e:
        print(f"⚠️ Erro ao atualizar aba Guerra_Atual: {e}")

# --- SISTEMA DE LOGS DA STAFF ---
async def enviar_log_staff(guild, mensagem):
    canal_logs_id = CACHE_CONFIG.get("Canal_Logs_ID")
    if canal_logs_id and str(canal_logs_id).isdigit():
        canal = guild.get_channel(int(canal_logs_id))
        if canal:
            try:
                await canal.send(f"📋 **Auditoria G59:** {mensagem}")
            except: pass

# --- INTELIGÊNCIA DO PAINEL ---
def gerar_texto_painel(guild):
    hora_corte_str = CACHE_CONFIG.get("Horario_Abre", "22:10")
    hora_corte, minuto_corte = 22, 10
    try:
        partes = hora_corte_str.split(":")
        hora_corte, minuto_corte = int(partes[0]), int(partes[1])
    except: pass

    agora = datetime.now(BR_TIMEZONE)
    if agora.hour < hora_corte or (agora.hour == hora_corte and agora.minute < minuto_corte):
        data_alvo = agora
    else:
        data_alvo = agora + timedelta(days=1)
        
    dia_nome = DIAS_DA_SEMANA_PT[data_alvo.weekday()]
    data_formatada = data_alvo.strftime("%d/%m")

    limites = RUNTIME["limites_atuais"]
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
    preset_atual_nome = RUNTIME["preset_ativo"]
    travas_dict = {}
    if preset_atual_nome and preset_atual_nome in CACHE_PRESETS:
        for item in CACHE_PRESETS[preset_atual_nome]:
            travas_dict[item["classe"].lower()] = item["travas"]

    for cat, max_vagas in limites.items():
        max_vagas = int(max_vagas)
        if max_vagas <= 0: continue

        categorias_visiveis += 1
        inscritos = presencas_ativas.get(cat, [])
        vagas_texto = ", ".join([f"<@{uid}>" for uid in inscritos]) if inscritos else "-"

        cat_lower = cat.lower().strip()
        req_cargo = "Liberado"
        trava_id = travas_dict.get(cat_lower, "")
        if trava_id:
            req_cargo = f"<@&{trava_id}>"

        embed.add_field(name=f"{cat} ({len(inscritos)}/{max_vagas})", value=f"🔒 **{req_cargo}**\n{vagas_texto}", inline=True)

    if categorias_visiveis == 0:
        embed.description = "⚠️ Nenhuma categoria ativa com vagas abertas para esta Guerra."

    embed.add_field(name="​", value="⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯", inline=False)
    texto_wait = "\n".join([f"⏳ #{i+1} <@{j['user_id']}> ➔ **{j['funcao']}**" for i, j in enumerate(wait_list_geral)]) if wait_list_geral else "*Fila vazia*"
    embed.add_field(name="⏳ Waitlist / Fila", value=texto_wait, inline=False)
    
    return embed

async def notificar_promovido_dm(bot_client, user_id, guild_id, classe_nome):
    try:
        msg_custom = CACHE_CONFIG.get("Msg_Promocao", "Você foi promovido da fila de espera e convocado para a GUERRA! Garanta sua participação.")
        user = await bot_client.fetch_user(user_id)
        await user.send(msg_custom)
    except Exception: pass

# --- FUNCIONAMENTO DOS BOTÕES ---
class BotaoClasseLista(discord.ui.Button):
    def __init__(self, label, row, custom_id):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=row, custom_id=custom_id)

    async def callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        cat_nome = self.label

        limites = RUNTIME["limites_atuais"]
        limite = int(limites.get(cat_nome, 0))

        if limite <= 0:
            return await interaction.response.send_message("❌ Esta categoria não possui vagas disponíveis.", ephemeral=True)

        preset_atual_nome = RUNTIME["preset_ativo"]
        trava_id = None
        if preset_atual_nome and preset_atual_nome in CACHE_PRESETS:
            for item in CACHE_PRESETS[preset_atual_nome]:
                if item["classe"].lower().strip() == cat_nome.lower().strip():
                    trava_id = item["travas"]
                    break

        if trava_id and str(trava_id).isdigit():
            cargo_obj = interaction.guild.get_role(int(trava_id))
            has_role = False
            if cargo_obj and cargo_obj in interaction.user.roles:
                has_role = True
            else:
                has_role = any(str(r.id) == str(trava_id) for r in getattr(interaction.user, 'roles', []))
                
            if not has_role:
                return await interaction.response.send_message(f"❌ Acesso Negado! Você precisa do cargo <@&{trava_id}> para se inscrever.", ephemeral=True)

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

        nova_view = GradeBotoesView()
        await interaction.response.edit_message(embed=gerar_texto_painel(interaction.guild), view=nova_view)
        await interaction.followup.send(msg_resposta, ephemeral=True)
        asyncio.create_task(atualizar_planilha_guerra_background())

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
            nova_view = GradeBotoesView()
            await interaction.response.edit_message(embed=gerar_texto_painel(interaction.guild), view=nova_view)
            await interaction.followup.send("👋 Você removeu a sua inscrição.", ephemeral=True)
            asyncio.create_task(atualizar_planilha_guerra_background())
        else:
            await interaction.response.send_message("⚠️ Você não está inscrito.", ephemeral=True)

class GradeBotoesView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        limites = RUNTIME["limites_atuais"]
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
    global presencas_ativas, wait_list_geral

    if RUNTIME["painel_msg_id"] and RUNTIME["canal_automacao_id"]:
        try:
            c = guild.get_channel(int(RUNTIME["canal_automacao_id"]))
            if c:
                try:
                    m = await c.fetch_message(int(RUNTIME["painel_msg_id"]))
                    await m.delete()
                except: pass
                if RUNTIME["aviso_msg_id"]:
                    try:
                        m_aviso = await c.fetch_message(int(RUNTIME["aviso_msg_id"]))
                        await m_aviso.delete()
                    except: pass
        except: pass

    RUNTIME["limites_atuais"].clear()
    RUNTIME["preset_ativo"] = nome_preset
    
    if nome_preset in CACHE_PRESETS:
        for item in CACHE_PRESETS[nome_preset]:
            RUNTIME["limites_atuais"][item["classe"]] = item["limite"]
    else:
        for cat in CATEGORIAS_PADRAO_INICIAIS:
            RUNTIME["limites_atuais"][cat] = 0

    presencas_ativas = {cat: [] for cat in RUNTIME["limites_atuais"].keys()}
    wait_list_geral.clear()

    cargo_id_str = CACHE_CONFIG.get("Cargo_Membro_ID", "")
    mencao = f"<@&{cargo_id_str}>" if cargo_id_str else "@here"
    
    view = GradeBotoesView()
    embed_visual = gerar_texto_painel(guild)
    embed_visual.set_footer(text=f"Estratégia aplicada: Preset [{nome_preset}]")
    
    msg_painel = await canal.send(embed=embed_visual, view=view)
    msg_abertura = CACHE_CONFIG.get("Msg_Abertura", "⚔️ **PAINEL DE GUERRA ABERTO!**")
    msg_aviso = await canal.send(f"{mencao} {msg_abertura}")

    RUNTIME["painel_msg_id"] = msg_painel.id
    RUNTIME["aviso_msg_id"] = msg_aviso.id
    RUNTIME["canal_automacao_id"] = canal.id
    
    asyncio.create_task(atualizar_planilha_guerra_background())

async def ejecutar_encerramento_sistema(guild, canal_fallback):
    global presencas_ativas
    
    painel_id = RUNTIME["painel_msg_id"]
    aviso_id = RUNTIME["aviso_msg_id"]
    canal_id = RUNTIME["canal_automacao_id"]
    
    canal_alvo = None
    if canal_id:
        try:
            canal_alvo = guild.get_channel(int(canal_id)) or await guild.fetch_channel(int(canal_id))
        except: pass
        
    if not canal_alvo: canal_alvo = canal_fallback

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

    RUNTIME["painel_msg_id"] = None
    RUNTIME["aviso_msg_id"] = None
    
    for k in presencas_ativas.keys():
        presencas_ativas[k] = []
    wait_list_geral.clear()
    
    asyncio.create_task(atualizar_planilha_guerra_background())
    await canal_fallback.send("🛑 **A GUERRA foi encerrada! O painel foi fechado.**")

# --- PAINEL SUPREMO DA STAFF (/help) ---
class ModalAbrirPainel(discord.ui.Modal, title="Abrir Painel de Guerra"):
    preset_nome = discord.ui.TextInput(
        label="Nome do Preset (Igual à planilha)",
        placeholder="Ex: T1, T2, NODEWAR...",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        canal_id = CACHE_CONFIG.get("Canal_Painel_ID")
        canal_alvo = interaction.guild.get_channel(int(canal_id)) if canal_id and str(canal_id).isdigit() else interaction.channel
        
        if not canal_alvo:
            return await interaction.followup.send("❌ Canal oficial não encontrado. Verifique o ID na folha de cálculo.", ephemeral=True)
            
        await ejecutar_criacao_sistema(interaction.guild, canal_alvo, self.preset_nome.value)
        await interaction.followup.send(f"✅ Painel [{self.preset_nome.value}] aberto com sucesso no canal <#{canal_alvo.id}>!", ephemeral=True)
        await enviar_log_staff(interaction.guild, f"{interaction.user.mention} forçou a abertura manual do painel usando o preset **{self.preset_nome.value}**.")

# --- PAINEL SUPREMO DA STAFF (/help) ---
class ViewPainelStaff(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        # 🔗 Botão de Planilha REMOVIDO permanentemente a pedido da Administração!

    @discord.ui.button(label="🔄 Sincronizar", style=discord.ButtonStyle.primary, custom_id="btn_staff_sync", row=0)
    async def btn_sync(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        sucesso, msg = await sincronizar_planilha()
        await interaction.followup.send(msg, ephemeral=True)
        await enviar_log_staff(interaction.guild, f"{interaction.user.mention} sincronizou as configurações com o Banco de Dados.")

    @discord.ui.button(label="▶️ Abrir Painel", style=discord.ButtonStyle.success, custom_id="btn_staff_abrir", row=0)
    async def btn_abrir(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ModalAbrirPainel())

    @discord.ui.button(label="🛑 Fechar Painel", style=discord.ButtonStyle.danger, custom_id="btn_staff_fechar", row=0)
    async def btn_fechar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        canal_id = CACHE_CONFIG.get("Canal_Painel_ID")
        canal_alvo = interaction.guild.get_channel(int(canal_id)) if canal_id and str(canal_id).isdigit() else interaction.channel
        if not canal_alvo:
            return await interaction.followup.send("❌ Canal oficial não encontrado.", ephemeral=True)
            
        await ejecutar_encerramento_sistema(interaction.guild, canal_alvo)
        await interaction.followup.send("✅ Painel encerrado com sucesso!", ephemeral=True)
        await enviar_log_staff(interaction.guild, f"{interaction.user.mention} encerrou o painel de guerra manualmente.")

    @discord.ui.button(label="📊 Relatório (Em Breve)", style=discord.ButtonStyle.secondary, custom_id="btn_staff_rel", row=1)
    async def btn_relatorio(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🚧 Esta função puxará a frequência da planilha! (Fase 4)", ephemeral=True)

@bot.tree.command(name="help", description="🛠️ Abre o Painel Supremo de Auditoria e Controle da Staff")
async def help_cmd(interaction: discord.Interaction):
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao:
        return await interaction.response.send_message("❌ Acesso Negado! Apenas a Staff pode usar este comando.", ephemeral=True)
    
    c_membro = f"<@&{CACHE_CONFIG.get('Cargo_Membro_ID')}>" if CACHE_CONFIG.get('Cargo_Membro_ID') else "❌ `Não configurado`"
    c_auto = f"<#{CACHE_CONFIG.get('Canal_Painel_ID')}>" if CACHE_CONFIG.get('Canal_Painel_ID') else "❌ `Não configurado`"
    c_logs = f"<#{CACHE_CONFIG.get('Canal_Logs_ID')}>" if CACHE_CONFIG.get('Canal_Logs_ID') else "❌ `Não configurado`"
    h_abre = CACHE_CONFIG.get("Horario_Abre", "22:10")
    h_fecha = CACHE_CONFIG.get("Horario_Fecha", "22:05")
    
    status_motor = CACHE_CONFIG.get("Automacao_Ativa", "1")
    status_auto = "🟢 `ATIVO` (Monitorando Horários)" if str(status_motor) == "1" else "🛑 `PAUSADO` (Loops suspensos)"

    embed_auditoria = discord.Embed(
        title="👑 G59 | PAINEL SUPREMO DE AUDITORIA",
        description="⚙️ **Verificação e status operacional do Banco de Dados.**",
        color=discord.Color.from_rgb(255, 215, 0)
    )
    embed_auditoria.add_field(
        name="🌐 Infraestrutura Core", 
        value=f"🔹 **Status do Motor:** {status_auto}\n🔹 **Cargo de Membro:** {c_membro}\n🔹 **Canal do Painel:** {c_auto}\n🔹 **Canal de Logs:** {c_logs}", 
        inline=False
    )
    embed_auditoria.add_field(
        name="⏳ Cronometragem e Loops", 
        value=f"⏰ **Abertura Automática:** `{h_abre}`\n⏰ **Fechamento Automático:** `{h_fecha}`", 
        inline=False
    )
    
    msg_abertura = CACHE_CONFIG.get("Msg_Abertura", "⚔️ **PAINEL DE GUERRA ABERTO!**")
    msg_promocao = CACHE_CONFIG.get("Msg_Promocao", "Você foi promovido da fila de espera!")
    embed_auditoria.add_field(
        name="💬 Mensagens Personalizadas Atuais", 
        value=f"**Aviso de Abertura:**\n*{msg_abertura}*\n\n**DM de Promoção da Fila:**\n*{msg_promocao}*", 
        inline=False
    )

    embed_crono = discord.Embed(
        title="🗓️ Cronograma Semanal de Guerras",
        description="Distribuição dos Presets que o bot abrirá automaticamente por dia.",
        color=discord.Color.blue()
    )
    
    crono_texto = ""
    for i in range(7):
        dia = DIAS_DA_SEMANA_PT[i]
        preset_dia = CACHE_CRONOGRAMA.get(dia, "")
        if not preset_dia or str(preset_dia).lower() in ["none", "folga", "descanso", ""]:
            crono_texto += f"**{dia}:** 💤 `Descanso / Sem Guerra`\n"
        else:
            crono_texto += f"**{dia}:** ⚔️ Preset `[{preset_dia}]`\n"
            
    embed_crono.add_field(name="Escala Configurada", value=crono_texto, inline=False)

    embed_comandos = discord.Embed(
        title="🛠️ Ações Rápidas & Comandos",
        description="Utilize os botões abaixo ou digite os comandos de barra (`/`) no chat.",
        color=discord.Color.purple()
    )
    embed_comandos.add_field(
        name="📜 Guia de Comandos do Bot",
        value=(
            "**`/config_geral`** ➔ Configura canais, cargos e liga/desliga o motor.\n"
            "**`/config_mensagens`** ➔ Edita os textos que o bot fala no chat e no privado.\n"
            "**`/cronograma_configurar`** ➔ Adiciona um Preset num dia da semana.\n"
            "**`/preset_configurar`** ➔ Adiciona ou edita vagas de uma classe em um Preset.\n"
            "**`/preset_remover`** ➔ Deleta uma classe de um Preset.\n"
            "**`/abrir_painel_teste`** ➔ Força a abertura de um painel de guerra agora.\n"
            "**`/fechar_painel_teste`** ➔ Força o encerramento da guerra atual.\n"
            "**`/sync`** ➔ Sincroniza o bot (Usado automaticamente pelos outros comandos).\n"
        ),
        inline=False
    )
    embed_comandos.set_footer(text="G59 Database Solutions - 100% Controlado via Discord")
    
    view = ViewPainelStaff()
    await interaction.response.send_message(embeds=[embed_auditoria, embed_crono, embed_comandos], view=view, ephemeral=True)

# --- COMANDOS PARA A CONFIGURAÇÃO REMOTA (HEADLESS DATABASE) ---

@bot.tree.command(name="config_geral", description="⚙️ Altera configurações estruturais (Canais, Cargos, Horários e Motor).")
@discord.app_commands.describe(
    configuracao="O que você deseja alterar?",
    valor="O ID (apenas números) ou Horário (ex: 22:00)"
)
@discord.app_commands.choices(configuracao=[
    discord.app_commands.Choice(name="Canal Oficial do Painel", value="Canal_Painel_ID"),
    discord.app_commands.Choice(name="Canal de Logs (Auditoria)", value="Canal_Logs_ID"),
    discord.app_commands.Choice(name="Cargo Oficial de Membro", value="Cargo_Membro_ID"),
    discord.app_commands.Choice(name="Horário de Abertura (ex: 22:10)", value="Horario_Abre"),
    discord.app_commands.Choice(name="Horário de Fechamento (ex: 23:00)", value="Horario_Fecha"),
    discord.app_commands.Choice(name="Motor Automático (1=LIGADO, 0=DESLIGADO)", value="Automacao_Ativa")
])
async def config_geral_cmd(interaction: discord.Interaction, configuracao: discord.app_commands.Choice[str], valor: str):
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao:
        return await interaction.response.send_message("❌ Acesso Negado!", ephemeral=True)
        
    await interaction.response.defer(ephemeral=True)
    try:
        aba = planilha.worksheet("Config_Geral")
        dados = aba.get_all_values()
        chave = configuracao.value
        linha_alvo = None
        
        for i, linha in enumerate(dados):
            if i > 0 and len(linha) > 0 and linha[0].strip() == chave:
                linha_alvo = i + 1
                break
                
        if linha_alvo:
            aba.update_acell(f"B{linha_alvo}", valor)
        else:
            aba.append_row([chave, valor])
            
        await sincronizar_planilha()
        await interaction.followup.send(f"✅ Configuração de **{configuracao.name}** atualizada com sucesso para `{valor}`!", ephemeral=True)
        await enviar_log_staff(interaction.guild, f"{interaction.user.mention} alterou **{configuracao.name}** para `{valor}`.")
    except Exception as e:
        await interaction.followup.send(f"❌ Erro ao salvar no banco de dados: {e}", ephemeral=True)

@bot.tree.command(name="config_mensagens", description="💬 Altera as mensagens automáticas enviadas pelo bot.")
@discord.app_commands.describe(
    tipo="Qual mensagem deseja alterar?",
    mensagem="Escreva o texto completo da mensagem."
)
@discord.app_commands.choices(tipo=[
    discord.app_commands.Choice(name="Aviso de Abertura (Chat)", value="Msg_Abertura"),
    discord.app_commands.Choice(name="Promoção da Fila (DM)", value="Msg_Promocao")
])
async def config_mensagens_cmd(interaction: discord.Interaction, tipo: discord.app_commands.Choice[str], mensagem: str):
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao: return await interaction.response.send_message("❌ Acesso Negado!", ephemeral=True)
        
    await interaction.response.defer(ephemeral=True)
    try:
        aba = planilha.worksheet("Config_Geral")
        dados = aba.get_all_values()
        chave = tipo.value
        linha_alvo = None
        
        for i, linha in enumerate(dados):
            if i > 0 and len(linha) > 0 and linha[0].strip() == chave:
                linha_alvo = i + 1
                break
                
        if linha_alvo:
            aba.update_acell(f"B{linha_alvo}", mensagem)
        else:
            aba.append_row([chave, mensagem])
            
        await sincronizar_planilha()
        await interaction.followup.send(f"✅ Nova **{tipo.name}** registrada com sucesso!", ephemeral=True)
        await enviar_log_staff(interaction.guild, f"{interaction.user.mention} atualizou os textos da **{tipo.name}**.")
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {e}", ephemeral=True)

@bot.tree.command(name="cronograma_configurar", description="🗓️ Define qual preset será aberto em cada dia da semana.")
@discord.app_commands.describe(
    dia="Selecione o dia da semana",
    preset="Nome do preset (ex: T1-25) ou 'Folga' para cancelar"
)
@discord.app_commands.choices(dia=[
    discord.app_commands.Choice(name="Segunda-feira", value="Segunda"),
    discord.app_commands.Choice(name="Terça-feira", value="Terca"),
    discord.app_commands.Choice(name="Quarta-feira", value="Quarta"),
    discord.app_commands.Choice(name="Quinta-feira", value="Quinta"),
    discord.app_commands.Choice(name="Sexta-feira", value="Sexta"),
    discord.app_commands.Choice(name="Sábado", value="Sabado"),
    discord.app_commands.Choice(name="Domingo", value="Domingo")
])
async def cronograma_configurar_cmd(interaction: discord.Interaction, dia: discord.app_commands.Choice[str], preset: str):
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao: return await interaction.response.send_message("❌ Acesso Negado!", ephemeral=True)
        
    await interaction.response.defer(ephemeral=True)
    try:
        aba = planilha.worksheet("Cronograma")
        dados = aba.get_all_values()
        chave = dia.value
        linha_alvo = None
        
        for i, linha in enumerate(dados):
            if i > 0 and len(linha) > 0 and linha[0].strip() == chave:
                linha_alvo = i + 1
                break
                
        if linha_alvo:
            aba.update_acell(f"B{linha_alvo}", preset)
        else:
            aba.append_row([chave, preset])
            
        await sincronizar_planilha()
        await interaction.followup.send(f"🗓️ O dia **{dia.name}** foi configurado para rodar o preset **{preset}**.", ephemeral=True)
        await enviar_log_staff(interaction.guild, f"{interaction.user.mention} definiu o preset **{preset}** para rodar na **{dia.name}**.")
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {e}", ephemeral=True)

@bot.tree.command(name="preset_configurar", description="⚙️ Cria ou atualiza uma classe/vaga dentro de um Preset.")
@discord.app_commands.describe(preset_nome="Nome do Preset", classe="Nome da Classe", vagas="Vagas liberadas", cargo_trava="Cargo obrigatório (Opcional)")
async def preset_configurar(interaction: discord.Interaction, preset_nome: str, classe: str, vagas: int, cargo_trava: discord.Role = None):
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao: return await interaction.response.send_message("❌ Acesso Negado!", ephemeral=True)
        
    await interaction.response.defer(ephemeral=True)
    try:
        aba_presets = planilha.worksheet("Setup_Presets")
        dados = aba_presets.get_all_values()
        
        preset_upper = preset_nome.upper().strip()
        classe_upper = classe.upper().strip()
        trava_id = str(cargo_trava.id) if cargo_trava else ""
        
        linha_encontrada = None
        for i, linha in enumerate(dados):
            if i == 0: continue
            if len(linha) >= 2 and linha[0].upper().strip() == preset_upper and linha[1].upper().strip() == classe_upper:
                linha_encontrada = i + 1
                break
        
        if linha_encontrada:
            aba_presets.update(f"C{linha_encontrada}:D{linha_encontrada}", [[vagas, trava_id]])
            msg = f"🔄 Vaga de **{classe_upper}** ATUALIZADA no preset **{preset_upper}** (Vagas: {vagas})."
        else:
            aba_presets.append_row([preset_upper, classe_upper, vagas, trava_id])
            msg = f"✅ Nova vaga de **{classe_upper}** CRIADA no preset **{preset_upper}** (Vagas: {vagas})."
            
        await sincronizar_planilha()
        await interaction.followup.send(msg, ephemeral=True)
        await enviar_log_staff(interaction.guild, f"{interaction.user.mention} editou a classe **{classe_upper}** no Preset **{preset_upper}**.")
    except Exception as e:
        await interaction.followup.send(f"❌ Erro ao salvar: {e}", ephemeral=True)

@bot.tree.command(name="preset_remover", description="🗑️ Remove completamente uma classe de um Preset.")
@discord.app_commands.describe(preset_nome="Nome do Preset", classe="Nome da Classe a ser apagada")
async def preset_remover(interaction: discord.Interaction, preset_nome: str, classe: str):
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao: return await interaction.response.send_message("❌ Acesso Negado!", ephemeral=True)
        
    await interaction.response.defer(ephemeral=True)
    try:
        aba_presets = planilha.worksheet("Setup_Presets")
        dados = aba_presets.get_all_values()
        
        preset_upper = preset_nome.upper().strip()
        classe_upper = classe.upper().strip()
        
        linha_deletar = None
        for i, linha in enumerate(dados):
            if i == 0: continue
            if len(linha) >= 2 and linha[0].upper().strip() == preset_upper and linha[1].upper().strip() == classe_upper:
                linha_deletar = i + 1
                break
        
        if linha_deletar:
            aba_presets.delete_rows(linha_deletar)
            await sincronizar_planilha()
            await interaction.followup.send(f"🗑️ A classe **{classe_upper}** foi deletada do preset **{preset_upper}**.", ephemeral=True)
            await enviar_log_staff(interaction.guild, f"{interaction.user.mention} deletou **{classe_upper}** do Preset **{preset_upper}**.")
        else:
            await interaction.followup.send(f"⚠️ A classe **{classe_upper}** não foi encontrada no preset.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Erro ao acessar o banco: {e}", ephemeral=True)

@bot.tree.command(name="sync", description="🔄 Força o bot a baixar as novidades do Banco de Dados.")
async def sync_cmd(interaction: discord.Interaction):
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao: return await interaction.response.send_message("❌ Acesso Negado!", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)
    sucesso, mensagem = await sincronizar_planilha()
    await interaction.followup.send(mensagem)

@bot.tree.command(name="abrir_painel_teste", description="🧪 Abre o painel ignorando o horário")
async def abrir_painel(interaction: discord.Interaction, preset: str):
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao: return await interaction.response.send_message("❌ Acesso Negado!", ephemeral=True)
        
    await interaction.response.defer(ephemeral=True)
    await ejecutar_criacao_sistema(interaction.guild, interaction.channel, preset)
    await interaction.followup.send("✅ Painel gerado com sucesso!")
    await enviar_log_staff(interaction.guild, f"{interaction.user.mention} usou `/abrir_painel_teste` com o preset **{preset}**.")

@bot.tree.command(name="fechar_painel_teste", description="🧪 Fecha o painel atual instantaneamente")
async def fechar_painel(interaction: discord.Interaction):
    tem_permissao = interaction.user.guild_permissions.administrator or any("staff" in role.name.lower() for role in interaction.user.roles)
    if not tem_permissao: return await interaction.response.send_message("❌ Acesso Negado!", ephemeral=True)
        
    await interaction.response.defer(ephemeral=True)
    await ejecutar_encerramento_sistema(interaction.guild, interaction.channel)
    await interaction.followup.send("✅ Painel fechado e limpo.")
    await enviar_log_staff(interaction.guild, f"{interaction.user.mention} usou `/fechar_painel_teste`.")

# --- AUTOMATIZAÇÃO DE HORÁRIO ---
@tasks.loop(minutes=1)
async def verificador_horarios_loop():
    if not CACHE_CONFIG or not CACHE_CRONOGRAMA: return
    
    status_motor = CACHE_CONFIG.get("Automacao_Ativa", "1")
    if str(status_motor) != "1": return 
    
    agora = datetime.now(BR_TIMEZONE)
    dia_semana_atual = agora.weekday()
    dia_nome = DIAS_DA_SEMANA_PT[dia_semana_atual]
    
    hora_abre = CACHE_CONFIG.get("Horario_Abre")
    hora_fecha = CACHE_CONFIG.get("Horario_Fecha")
    hora_atual_str = agora.strftime("%H:%M")
    
    canal_id = CACHE_CONFIG.get("Canal_Painel_ID")
    if not canal_id: return
    
    guild = bot.guilds[0] if bot.guilds else None
    if not guild: return
    
    canal = guild.get_channel(int(canal_id))
    if not canal: return

    preset_de_hoje = CACHE_CRONOGRAMA.get(dia_nome)
    if preset_de_hoje and str(preset_de_hoje).lower() not in ["", "none", "folga", "descanso"]:
        if hora_atual_str == hora_abre and RUNTIME["painel_msg_id"] is None:
            asyncio.create_task(ejecutar_criacao_sistema(guild, canal, preset_de_hoje))
            await enviar_log_staff(guild, f"⏰ O sistema automático ABRIU o painel de guerra para o preset **{preset_de_hoje}**.")
            
        elif hora_atual_str == hora_fecha and RUNTIME["painel_msg_id"] is not None:
            asyncio.create_task(ejecutar_encerramento_sistema(guild, canal))
            await enviar_log_staff(guild, f"⏰ O sistema automático FECHOU o painel de guerra.")

@bot.event
async def on_ready():
    print(f"✅ Bot conectado como {bot.user}")
    await sincronizar_planilha()
    
    try:
        synced = await bot.tree.sync()
        print(f"🔄 {len(synced)} comandos globais oficiais sincronizados.")
    except Exception as e:
        print(f"⚠️ Erro ao sincronizar comandos: {e}")
        
    if not verificador_horarios_loop.is_running(): 
        verificador_horarios_loop.start()

keep_alive()
bot.run(os.environ.get("DISCORD_TOKEN"))
