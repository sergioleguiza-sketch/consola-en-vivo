import streamlit as st
import pandas as pd
from datetime import datetime, timezone, timedelta
from supabase import create_client

# Inyectamos el CSS para cambiar el color de los bordes de los contenedores
st.markdown(
    """
    <style>
    /* Aplicamos fondo a los contenedores con borde */
    [data-testid="stVerticalBlockBorderWrapper"] {
        background-color: #f0f2f6; /* Gris muy claro de base */
        padding: 20px;
        border-radius: 10px;
        border: none !important;
    }
    
    /* Si querés un color específico para la zona de Registro (azul clarito) */
    .stColumn > div > div > div[data-testid="stVerticalBlockBorderWrapper"] {
        border-left: 5px solid #0000FF !important; /* Una barrita azul al costado queda muy Pro */
    }
    </style>
    """,
    unsafe_allow_html=True
)

# 1. Configuración de Conexión y Página
st.set_page_config(layout="wide", page_title="BACKYARD ULTRA.ar EN VIVO - Consola de Control")
url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_KEY"]
supabase = create_client(url, key)

def finalizar_evento(id_evento):
    try:
        # Cambiamos el estado en la tabla 'eventos'
        supabase.table("eventos").update({"estado": "finalizado"}).eq("id_evento", id_evento).execute()
        return True
    except Exception as e:
        return False

def obtener_clasificacion_final(id_evento):
    try:
        # 1. Traemos las vueltas y estados (la acción)
        res_vueltas = supabase.table("vueltas_vivo").select(
            "dorsal, nro_vuelta, estado, hora_llegada"
        ).eq("id_evento", id_evento).execute()
        
        if not res_vueltas.data:
            return None

        df_vueltas = pd.DataFrame(res_vueltas.data)

        # 2. Traemos TODO de inscripciones y atletas (los datos maestros)
        # Según tu esquema, la relación es: vueltas_vivo -> inscripciones -> atletas
        res_master = supabase.table("inscripciones").select(
            "dorsal, atletas:dni_atleta(dni, nombre, apellido, fecha_nacimiento, genero, nacionalidad, localidad)"
        ).eq("id_evento", id_evento).execute()
        
        # Aplanamos la información del atleta para que sea una tabla simple
        data_master = []
        for i in res_master.data:
            at = i['atletas']
            data_master.append({
                "dorsal": i['dorsal'],
                "dni": at['dni'],
                "nombre": at['nombre'],
                "apellido": at['apellido'],
                "fecha_de_nacimiento": at['fecha_nacimiento'],
                "genero": at['genero'],
                "nacionalidad": at['nacionalidad'],
                "localidad": at['localidad']
            })
        df_master = pd.DataFrame(data_master)

        # 3. Unimos la acción con los datos maestros
        df = pd.merge(df_vueltas, df_master, on="dorsal", how="left")
        
        # 4. Agrupamos para obtener el resultado final por atleta
        clasif = df.groupby('dorsal').agg({
            'dni': 'first',
            'nro_vuelta': 'max',
            'nombre': 'first',
            'apellido': 'first',
            'fecha_de_nacimiento': 'first',
            'genero': 'first',
            'nacionalidad': 'first',
            'localidad': 'first',
            'estado': 'last',
            'hora_llegada': 'max'
        }).reset_index()

        # 5. Ordenamiento oficial Backyard (WINNER > Vueltas > Tiempo)
        clasif['es_winner'] = clasif['estado'] == 'WINNER'
        clasif = clasif.sort_values(
            by=['es_winner', 'nro_vuelta', 'hora_llegada'], 
            ascending=[False, False, True]
        )

        # 6. REORDENAMIENTO FINAL SOLICITADO (El formato Bitácora)
        # dni, vueltas, nombre, apellido, fecha de nacimiento, genero, nacionalidad y localidad
        clasif = clasif.rename(columns={'nro_vuelta': 'vueltas'})
        
        columnas_bitacora = [
            'dni', 'vueltas', 'nombre', 'apellido', 
            'fecha_de_nacimiento', 'genero', 'nacionalidad', 'localidad'
        ]
        
        return clasif[columnas_bitacora]

    except Exception as e:
        st.error(f"Error al generar formato Bitácora: {str(e)}")
        return None

# 2. Funciones de Lógica de Tiempo (Estricto Backyard)
def calcular_seguimiento_carrera(hora_cero_db):
    # 1. Convertimos la hora de Supabase y le restamos 3 horas (Argentina)
    inicio_carrera = datetime.fromisoformat(hora_cero_db.replace('Z', '+00:00')) - timedelta(hours=3)
    
    # 2. El 'ahora' también en UTC-3
    ahora = datetime.now(timezone.utc) - timedelta(hours=3)
    
    # 3. Cálculo de segundos totales transcurridos
    duracion = ahora - inicio_carrera
    segundos_totales = int(duracion.total_seconds())
    
    # Si la carrera no empezó, segundos_totales será negativo. Controlamos eso:
    if segundos_totales < 0:
        return 0, 0, "00:00:00"

    # Patio actual y tiempo del bloque
    patio_actual = (segundos_totales // 3600) + 1
    segundos_del_patio = segundos_totales % 3600
    
    tiempo_formateado = str(timedelta(seconds=segundos_totales))

    segundos_restantes = 3600 - segundos_del_patio

    # Lógica de llamados de corral (3', 2', 1')
    alerta = "EN CURSO"
    if 120 < segundos_restantes <= 180: alerta = "🚨 ¡3 MINUTOS! (1° LLAMADO)"
    elif 60 < segundos_restantes <= 120: alerta = "🚨 ¡2 MINUTOS! (2° LLAMADO)"
    elif 0 < segundos_restantes <= 60: alerta = "⚠️ ¡1 MINUTO! (ÚLTIMO LLAMADO)"
    
    return patio_actual, segundos_del_patio, tiempo_formateado, alerta, segundos_restantes

def procesar_entrada_y_registrar(id_evento, entrada_raw, patio_actual, hora_cero):
    # 1. ¿Es un dorsal manual (número corto)?
    if entrada_raw.isdigit() and len(entrada_raw) <= 4:
        dorsal_final = int(entrada_raw)
    else:
        # 2. Es un Chip o QR largo: lo buscamos en la base de datos
        # Buscamos en la nueva columna 'id_chip' que agregamos
        res = supabase.table("inscripciones").select("dorsal") \
            .eq("id_evento", id_evento) \
            .eq("id_chip", entrada_raw).execute()
        
        if res.data:
            dorsal_final = res.data[0]['dorsal']
        else:
            return f"❌ El código '{entrada_raw}' no está asignado a ningún atleta."

    # 3. Con el dorsal ya identificado, llamamos a tu lógica de Backyard
    return registrar_suceso_inteligente(id_evento, dorsal_final, patio_actual, hora_cero)
    
def registrar_suceso_inteligente(id_evento, dorsal, patio_sistema, hora_cero_db, estado_manual=None):
    # 1. Todo a local (Argentina)
    ahora = datetime.now(timezone.utc) - timedelta(hours=3)
    inicio_carrera = datetime.fromisoformat(hora_cero_db.replace('Z', '+00:00')) - timedelta(hours=3)
    
    # 2. Calculamos los segundos reales transcurridos desde el inicio
    segundos_desde_inicio = int((ahora - inicio_carrera).total_seconds())
    
    # Determinamos el patio del reloj (1 hora = 3600 seg)
    patio_reloj = (segundos_desde_inicio // 3600) + 1
    
    if estado_manual:
        estado = estado_manual
        if estado in ["DNS", "DNF (DNS)"]:
            segundos_netos = 0  # DNS siempre es 0
            nro_vuelta_registro = 0
            patio_final = 1
        else:
            # Para RTC, INC, etc., calculamos cuánto tiempo pasó en ese patio
            # Ej: si pasaron 3700 segundos en total, en el patio 2 lleva 100 segundos.
            segundos_netos = segundos_desde_inicio % 3600 
            nro_vuelta_registro = patio_sistema - 1
            patio_final = patio_sistema
    else:
        # Lógica automática (ACT / OVR)
        segundo_del_bloque = segundos_desde_inicio % 3600
        
        if 0 < segundo_del_bloque <= 300:
            estado = "DNF (OVR)"
            segundos_netos = 3600 # Se pasó de tiempo, le clavamos la hora justa
            nro_vuelta_registro = (segundos_desde_inicio // 3600) - 1
            #nro_vuelta_registro = patio_reloj - 2
            patio_final = patio_reloj
        else:
            estado = "ACT"
            segundos_netos = segundo_del_bloque
            nro_vuelta_registro = (segundos_desde_inicio // 3600) + 1
            #nro_vuelta_registro = patio_reloj
            patio_final = patio_reloj

    if nro_vuelta_registro < 0: nro_vuelta_registro = 0
        
    nuevo_registro = {
        "id_evento": id_evento, 
        "dorsal": dorsal, 
        "nro_vuelta": nro_vuelta_registro, 
        "hora_llegada": ahora.isoformat(), #hora_registro <--- USAMOS LA VARIABLE DINÁMICA
        "estado": estado,
        "segundos_netos": segundos_netos,
        "patio_suceso": patio_final 
    }
    
    try:
        supabase.table("vueltas_vivo").insert(nuevo_registro).execute()
        return f"✅ Bib {dorsal}: {estado} en Patio {nro_vuelta_registro}"
    except Exception as e:
        return f"❌ Error: El dorsal {dorsal} ya tiene un suceso registrado en el Patio {patio_final}."

def obtener_estado_monitor(id_evento, nro_vuelta):
    # 1. Traemos inscripciones: asistente está aquí, y anidamos atletas para el nombre
    query = "dorsal, asistente, atletas:dni_atleta(nombre, apellido)"
    ins = supabase.table("inscripciones").select(query).eq("id_evento", id_evento).execute()
    
    # 2. Traemos arribos y DNF del patio actual
    arr = supabase.table("vueltas_vivo").select("dorsal").eq("id_evento", id_evento).eq("nro_vuelta", nro_vuelta).execute()
    fuera = supabase.table("vueltas_vivo").select("dorsal").eq("id_evento", id_evento).neq("estado", "ACT").execute()
    
    dorsales_arribados = {a['dorsal'] for a in arr.data}
    dorsales_fuera = {f['dorsal'] for f in fuera.data}
    
    faltantes = []
    total_inscriptos = len(ins.data)
    
    for i in ins.data:
        d = i['dorsal']
        # Si no llegó y no está fuera, es un faltante
        if d not in dorsales_arribados and d not in dorsales_fuera:
            # Sacamos el nombre del atleta de la relación anidada
            nombre_completo = f"{i['atletas']['nombre']} {i['atletas']['apellido']}"
            asistente = i['asistente'] if i['asistente'] else "Sin asistente"
            faltantes.append(f"Bib {d} - {nombre_completo} | Asistente: {asistente}")
    
    en_circuito = len(faltantes)
    return faltantes, total_inscriptos, en_circuito

# --- AJUSTE PARA MÚLTIPLES EVENTOS EN CONSOLA ---

# 1. Traemos todos los eventos en vivo
res_eventos = supabase.table("eventos").select("*").eq("estado", "en_vivo").execute()
eventos_lista = res_eventos.data

if eventos_lista:
    # 2. Selector para el Director de Carrera
    if len(eventos_lista) > 1:
        nombres_eventos = [e['nombre'] for e in eventos_lista]
        seleccion = st.sidebar.selectbox("🎮 Seleccioná Carrera a Controlar:", nombres_eventos)
        evento = next(e for e in eventos_lista if e['nombre'] == seleccion)
    else:
        evento = eventos_lista[0]

    # 3. Definimos las variables que el resto del código ya usa
    ID_EVENTO = evento['id_evento']
    
    # 4. Cálculo de tiempo (Patio, crono, alertas)
    patio, crono, tiempo_total, alerta_msg, seg_restantes_evento = calcular_seguimiento_carrera(evento['hora_cero'])
    #patio, crono, seg_restantes, alerta_msg = calcular_seguimiento_carrera(evento['hora_cero'])

    # --- INTERFAZ DE CONSOLA ---
    st.title(f"⏱️ Panel de Control: {evento['nombre']}")
    st.markdown(f"### 📍 {evento['lugar']} <span style='margin: 0 15px;'>|</span> {alerta_msg}", unsafe_allow_html=True)
    
else:
    st.error("No hay eventos 'en_vivo' para controlar.")

    # --- SECCIÓN FUERA DEL BUCLE EN VIVO ---
    st.divider()
    with st.expander("📂 Consultar Eventos Finalizados y Descargar Resultados"):
        res_fin = supabase.table("eventos").select("*").eq("estado", "finalizado").execute()
        if res_fin.data:
            ev_nom = [e['nombre'] for e in res_fin.data]
            sel_fin = st.selectbox("Seleccioná evento para descargar:", ev_nom)
            ev_obj = next(e for e in res_fin.data if e['nombre'] == sel_fin)
            
            if st.button("Generar Clasificación Final", type="primary", use_container_width=True):
                df_final = obtener_clasificacion_final(ev_obj['id_evento'])
                if df_final is not None:
                    st.dataframe(df_final, use_container_width=True)
                    csv = df_final.to_csv(index=False).encode('utf-8')
                    st.download_button(
                        label="⬇️ Descargar CSV",
                        data=csv,
                        file_name=f"resultados_{ev_obj['nombre']}.csv",
                        mime='text/csv'
                    )
        else:
            st.info("No hay eventos finalizados todavía.")
    st.stop()

# --- 1. CÁLCULO UNIFICADO ---
# Llamamos a la función UNA SOLA VEZ para toda la página
faltantes_lista, total_starters, en_pista_count = obtener_estado_monitor(ID_EVENTO, patio)

# Calculamos los que REALMENTE están activos (Total - los que ya quedaron fuera)
# Para un Backyard, los 'Activos' son los que salieron a esta vuelta
# Buscamos los que terminaron la vuelta anterior (ej: patio - 1)
# Cálculo de Activos Reales para el Director:
# Lógica mejorada para Activos
# En un Backyard, los Activos son: Todos los que empezaron (Starters) 
# MENOS los que ya quedaron fuera (DNF, DQ, etc.) en cualquier momento de la carrera.
res_eliminados = supabase.table("vueltas_vivo") \
    .select("dorsal", count="exact") \
    .eq("id_evento", ID_EVENTO) \
    .neq("estado", "ACT") \
    .execute()

total_fuera = res_eliminados.count if res_eliminados.count is not None else 0
total_activos = total_starters - total_fuera

# Y para la métrica "En Circuito":
# Son los Activos que todavía no cruzaron la meta en ESTE patio.
llegaron_ya = supabase.table("vueltas_vivo") \
    .select("dorsal", count="exact") \
    .eq("id_evento", ID_EVENTO) \
    .eq("nro_vuelta", patio) \
    .eq("estado", "ACT") \
    .execute()
        
# 1. Contar cuántos tienen el estado 'ACT'
# Usamos el conteo exacto de la base de datos
#total_activos = res_activos.count if res_activos.count else 0
# Forzamos que si total_activos quedó en 0 por alguna razón, sea al menos el nro de starters
if total_activos == 0: total_activos = total_starters
ya_en_base = llegaron_ya.count if llegaron_ya.count is not None else 0
en_pista_real = total_activos - ya_en_base

# SECCIÓN A: MÉTRICAS PRINCIPALES
c1, c2, c3, c4 = st.columns(4) # Cambiamos a 4 columnas
with c1:
    st.metric("Starters", total_starters) # Mostramos el total inicial
with c2:
    st.metric("Vuelta Actual", patio)
with c3:
    st_color = "inverse" if seg_restantes_evento <= 180 else "normal"
    st.metric("Tiempo para Campana", crono, delta_color=st_color)
with c4:
    # Mantenemos tu lógica de "En Pista / Activos"
    st.metric("En Circuito / Activos", f"{en_pista_count} / {total_activos}")


with st.container(border=True):
    st.subheader("📲 Registro de Arribos")
    # Creamos dos columnas: una ancha para el scan y una angosta para el botón
    # 'vertical_alignment' hace que el botón se alinee al centro del input
    col_input, col_btn = st.columns([3, 1], vertical_alignment="bottom")
    
    with col_input:
        dorsal_scan = st.text_input("Escanear Dorsal o Chip", key="scan_input", placeholder="Ej: 7")
        
    with col_btn:
        if st.button("REGISTRAR ARRIBO", use_container_width=True, type="primary"):
            if dorsal_scan:
                # IMPORTANTE: Ya no usamos int(dorsal_scan) aquí, 
                # lo procesamos adentro de la función puente.
                resultado = procesar_entrada_y_registrar(
                    ID_EVENTO, 
                    dorsal_scan, # Va el texto tal cual sale del escáner
                    patio, 
                    evento['hora_cero']
                )
                
                if "✅" in resultado or "⚠️" in resultado:
                    st.toast(resultado)
                    # Pequeño truco: podemos limpiar el input aquí si fuera necesario
                    st.rerun()
                else:
                    st.error(resultado)
            else:
                st.warning("⚠️ Escanée un dorsal o chip primero")

# --- BLOQUE 1: GESTIÓN DE SUCESOS (Tu código actual mejorado) ---
with st.container(border=True):
    st.subheader("📝 Gestión de Sucesos (Manual)")
    
    # Mantenemos tu lógica de carga de atletas
    ins_data = supabase.table("inscripciones").select("dorsal, atletas(nombre, apellido)").eq("id_evento", ID_EVENTO).execute()
    opciones = [f"{c['dorsal']} - {c['atletas']['nombre']} {c['atletas']['apellido']}" for c in ins_data.data]
    selec = st.selectbox("Seleccionar Atleta para novedad:", opciones)
    dorsal_id = int(selec.split(" - ")[0])

    # Fila 1: Abandonos y Faltas
    b1, b2, b3, b4, b5 = st.columns(5)
    with b1:
        # DNS: Solo para el Patio 1 (No vino al evento)
        if st.button("🚫 DNS", help="Did Not Start (No vino)", use_container_width=True):
            st.toast(registrar_suceso_inteligente(ID_EVENTO, dorsal_id, 1, evento['hora_cero'], "DNS"))
            st.rerun()
    with b2:
        # RTC: El clásico "No salgo más" del Backyard
        if st.button("❌ RTC", help="Refuse To Continue (Abandono)", use_container_width=True):
            st.toast(registrar_suceso_inteligente(ID_EVENTO, dorsal_id, patio, evento['hora_cero'], "DNF (RTC)"))
            st.rerun()
    with b3:
        if st.button("⚠️ INC", help="Incomplete Loop", use_container_width=True):
            st.toast(registrar_suceso_inteligente(ID_EVENTO, dorsal_id, patio, evento['hora_cero'], "DNF (INC)"))
            st.rerun()
    # Fila 2: Acciones Especiales
    #b4, b5 = st.columns(2)
    with b4:
        if st.button("🚫 DQ", help="Disqualified", use_container_width=True):
            st.toast(registrar_suceso_inteligente(ID_EVENTO, dorsal_id, patio, evento['hora_cero'], "DNF (DQ)"))
            st.rerun()
    with b5:
        # Usamos un popover para que el botón de confirmación aparezca al hacer clic
        with st.popover("🏆 WINNER", use_container_width=True, help="Declarar ganador y finalizar evento"):
            st.warning("¿Estás seguro? Esto cerrará el evento y cambiará su estado a FINALIZADO.")
            
            if st.button("SÍ, FINALIZAR CARRERA", type="primary", use_container_width=True):
                # 1. Registramos al ganador
                registrar_suceso_inteligente(ID_EVENTO, dorsal_id, patio, evento['hora_cero'], "WINNER")
                
                # 2. Intentamos cerrar el evento
                if finalizar_evento(ID_EVENTO):
                    st.balloons()
                    st.success(f"¡Evento finalizado con éxito!")
                    # Pequeña pausa para que se vea el mensaje antes del rerun
                    import time
                    time.sleep(2)
                    st.rerun()
                    # Si el evento ya terminó, mostramos opción de descargar clasificación
                    if evento['estado'] == 'finalizado':
                        st.success("🏁 Este evento ha finalizado.")
                        
                        df_final = obtener_clasificacion_final(ID_EVENTO)
                        
                        if df_final is not None:
                            st.subheader("📊 Clasificación Final")
                            st.dataframe(df_final, use_container_width=True)
                            
                            # Botón para descargar CSV (formato ideal para la Bitácora o enviar afuera)
                            csv = df_final.to_csv(index=False).encode('utf-8')
                            st.download_button(
                                label="Descargar Clasificación (CSV)",
                                data=csv,
                                file_name=f"clasificacion_{evento['nombre']}_{datetime.now().strftime('%Y%m%d')}.csv",
                                mime='text/csv',
                            )
                else:
                    st.error("Error al actualizar el estado del evento en la base de datos.")

# --- BLOQUE 2: MONITOR DE SEGURIDAD (Lo que falta llegar) ---
with st.container(border=True):
    st.subheader("🏃‍♂️ Monitor de Seguridad (En Circuito)")
    
    # Aquí usamos la función que arreglamos antes para ver quién falta
    #faltantes, total, en_pista = obtener_estado_monitor(ID_EVENTO, patio)
    
    c_pista, c_total = st.columns(2)
    c_pista.metric("Atletas en Circuito", en_pista_count)
    c_total.metric("Total Activos", total_activos)
    
    if faltantes_lista:
        for f in faltantes_lista:
            st.warning(f)
    else:
        st.success("✅ ¡Patio Completo!")
