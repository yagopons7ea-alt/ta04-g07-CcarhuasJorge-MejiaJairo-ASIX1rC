#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generar_incidencias.py  -  Generador de incidencias sintéticas para IncidentIQ
==============================================================================

Genera (por defecto) 4.000 incidencias informáticas aleatorias, realistas y
coherentes entre sí, siguiendo las preguntas y las opciones REALES del
formulario de Google Forms "Incidències equips informàtics".

No necesita Internet, ni Google Forms, ni ninguna librería externa
(si "rich" está instalado, se usa para mostrar el resumen; si no, se imprime
en texto plano).

Ficheros que crea (en el directorio indicado con --sortida, por defecto ".")
    incidencies.csv    -> obligatorio
    incidencies.json   -> opcional (--formats)
    incidencies.xml    -> opcional (--formats)

Uso básico
    python3 generar_incidencias.py
    python3 generar_incidencias.py --total 4000 --seed 42
    python3 generar_incidencias.py --formats csv            # solo el CSV
    python3 generar_incidencias.py --capcaleres respostes.csv

Compatibilidad con IncidentIQ
    * El orden de las 15 columnas es el del formulario.
    * Los nombres de las columnas están en la constante CAPCALERES_PER_DEFECTE
      (un único sitio). Si los encabezados reales de tu proyecto son otros,
      puedes cambiarlos ahí o, mejor, pasar un CSV real con --capcaleres:
      el programa copia su primera fila (tiene que tener 15 columnas).
    * Todos los valores categóricos son EXACTAMENTE los del formulario
      (con sus tildes, mayúsculas y puntos finales, p. ej. "No ho sé.").
    * Solo se usan caracteres latinos básicos/extendidos (sin comillas
      tipográficas, sin saltos de línea, sin caracteres de control).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

# ============================================================================
# 1. CONFIGURACIÓN GENERAL
# ============================================================================

TOTAL_PER_DEFECTE = 4000
SEED_PER_DEFECTE = 42              # misma semilla => mismos datos (reproducible)
NOM_BASE_PER_DEFECTE = "incidencies"
DIES_INTERVAL_PER_DEFECTE = 90     # amplitud del periodo de registros

# Formatos de fecha (los de una hoja de cálculo de Google en catalán/español).
FORMAT_MARCA_TEMPS = "%d/%m/%Y %H:%M:%S"
FORMAT_DATA = "%d/%m/%Y"

# Orden de los campos (= orden de las preguntas del formulario).
CAMPS = [
    "marca_temps",      # 1  Fecha y hora de registro
    "email",            # 2  Correo electrónico
    "data_incidencia",  # 3  P1  Quin dia has detectat el problema?
    "ubicacio",         # 4  P2  On és l'equip que té el problema?
    "dispositiu",       # 5  P3  Quin equip té el problema?
    "simptoma",         # 6  P4  Què li passa a l'equip?
    "descripcio",       # 7  P5  Ens pots explicar què passa?
    "inici",            # 8  P6  Quan va començar el problema?
    "impacte",          # 9  P7  Pots continuar treballant amb aquest equip?
    "afectats",         # 10 P8  A qui afecta el problema?
    "accions",          # 11 P9  Què has provat de fer per solucionar-ho?
    "repeticio",        # 12 P10 Aquest problema ja havia passat abans?
    "altre_equip",      # 13 P11 Pots utilitzar un altre equip mentre esperes?
    "hi_ha_error",      # 14 P12 Apareix algun missatge d'error a la pantalla?
    "text_error",       # 15 P13 Si has vist un missatge d'error, què hi diu?
]

# Encabezados del CSV. Son los de una exportación habitual de Google Forms a
# CSV (columna de marca de tiempo + correo + títulos de las preguntas).
# >>> Si tus programas esperan otros nombres, cámbialos aquí o usa --capcaleres.
CAPCALERES_PER_DEFECTE = [
    "Marca de temps",
    "Adreça electrònica",
    "Quin dia has detectat el problema?",
    "On és l'equip que té el problema?",
    "Quin equip té el problema?",
    "Què li passa a l'equip?",
    "Ens pots explicar què passa?",
    "Quan va començar el problema?",
    "Pots continuar treballant amb aquest equip?",
    "A qui afecta el problema?",
    "Què has provat de fer per solucionar-ho?",
    "Aquest problema ja havia passat abans?",
    "Pots utilitzar un altre equip mentre esperes?",
    "Apareix algun missatge d'error a la pantalla?",
    "Si has vist un missatge d'error, què hi diu?",
]

# ============================================================================
# 2. OPCIONES REALES DEL FORMULARIO
# ============================================================================

# --- P2: ubicación ---------------------------------------------------------
UBICACIONS = {                       # peso base (más peso = más frecuente)
    "Aula 101": 17,
    "Aula 102": 16,
    "Laboratori d'informàtica": 20,
    "Biblioteca": 12,
    "Administració": 14,
    "Sala de reunions": 10,
    "Altres": 11,
}

# --- P3: dispositivo -------------------------------------------------------
D_SOBRETAULA = "Ordinador de sobretaula"
D_PORTATIL = "Ordinador portàtil"
D_PANTALLA = "Pantalla"
D_TECLAT = "Teclat"
D_RATOLI = "Ratolí"
D_IMPRESSORA = "Impressora"
D_PROJECTOR = "Projector"
D_WIFI = "Wi-Fi"
D_ALTAVEUS = "Altaveus o micròfon"
D_ALTRE = "Un altre equip"

DISPOSITIUS = {                      # más ordenadores, impresoras y Wi-Fi
    D_SOBRETAULA: 24,
    D_PORTATIL: 20,
    D_IMPRESSORA: 16,
    D_WIFI: 14,
    D_PROJECTOR: 7,
    D_RATOLI: 5,
    D_TECLAT: 5,
    D_PANTALLA: 5,
    D_ALTAVEUS: 3,
    D_ALTRE: 1,
}

# --- P4: síntoma -----------------------------------------------------------
S_NO_ENCEN = "No s'encén"
S_LENT = "Va molt lent"
S_INTERNET = "No funciona Internet"
S_PANTALLA = "La pantalla no es veu bé"
S_SO = "No se sent el so"
S_IMPRIMEIX = "No imprimeix"
S_TRENCADA = "Hi ha alguna peça trencada"
S_NOSE = "No ho sé"
S_ALTRA = "Una altra cosa"

# --- P6: cuándo empezó -----------------------------------------------------
I_AVUI = "Avui"
I_AHIR = "Ahir"
I_DIES = "Fa uns quants dies"
I_SETMANA = "Fa més d'una setmana"
I_NOSE = "No ho sé"
OPCIONS_INICI = {I_AVUI: 30, I_AHIR: 24, I_DIES: 24, I_SETMANA: 14, I_NOSE: 8}

# --- P7: impacto -----------------------------------------------------------
IMP_SI = "Sí, sense problemes"
IMP_DIF = "Sí, però amb dificultats"
IMP_NO = "No, no puc treballar"
IMP_NOSE = "No ho sé"

# --- P8: afectados ---------------------------------------------------------
A_MI = "Només a mi"
A_ALTRES = "A mi i a altres persones"
A_TOTS = "A tota l'aula o al departament"
A_NOSE = "No ho sé"

# --- P9: acciones previas --------------------------------------------------
AC_REINICI = "Apagar i tornar a encendre l'equip"
AC_CABLES = "Revisar si els cables estan connectats"
AC_INTERNET = "Comprovar si hi ha Internet"
AC_REPROVAR = "Tornar-ho a provar"
AC_RES = "No he provat res"
AC_ALTRA = "Una altra cosa"

# --- P10: ya ocurrió -------------------------------------------------------
R_SI = "Sí"
R_NO = "No"
R_NOSE = "No ho sé."                 # (con punto final, como en el formulario)

# --- P11: otro equipo ------------------------------------------------------
E_SI = "Sí"
E_PARCIAL = "Sí, però no per fer-ho tot."
E_NO = "No"
E_NOSE = "No ho sé."

# --- P12: mensaje de error (en el formulario "Si" va SIN tilde) -------------
M_SI = "Si"
M_NO = "No"
M_NOSE = "No ho sé"
M_SENSE_PANTALLA = "L'equip no té pantalla"

# Listas de valores válidos por campo (para validar cada registro).
OPCIONS_VALIDES = {
    "ubicacio": set(UBICACIONS),
    "dispositiu": set(DISPOSITIUS),
    "simptoma": {S_NO_ENCEN, S_LENT, S_INTERNET, S_PANTALLA, S_SO, S_IMPRIMEIX,
                 S_TRENCADA, S_NOSE, S_ALTRA},
    "inici": set(OPCIONS_INICI),
    "impacte": {IMP_SI, IMP_DIF, IMP_NO, IMP_NOSE},
    "afectats": {A_MI, A_ALTRES, A_TOTS, A_NOSE},
    "accions": {AC_REINICI, AC_CABLES, AC_INTERNET, AC_REPROVAR, AC_RES, AC_ALTRA},
    "repeticio": {R_SI, R_NO, R_NOSE},
    "altre_equip": {E_SI, E_PARCIAL, E_NO, E_NOSE},
    "hi_ha_error": {M_SI, M_NO, M_NOSE, M_SENSE_PANTALLA},
}

# ============================================================================
# 3. REGLAS DE COHERENCIA (pesos entre respuestas relacionadas)
# ============================================================================

# Qué dispositivos son más probables en cada ubicación (multiplicador).
LOC_DISP = {
    "Aula 101": {D_PROJECTOR: 2.0, D_ALTAVEUS: 1.6, D_SOBRETAULA: 1.1,
                 D_IMPRESSORA: 0.3, D_WIFI: 1.2},
    "Aula 102": {D_PROJECTOR: 2.0, D_ALTAVEUS: 1.6, D_SOBRETAULA: 1.1,
                 D_IMPRESSORA: 0.3, D_WIFI: 1.2},
    "Laboratori d'informàtica": {D_SOBRETAULA: 2.0, D_PORTATIL: 0.6,
                                 D_RATOLI: 1.6, D_TECLAT: 1.6, D_PANTALLA: 1.6,
                                 D_IMPRESSORA: 0.8, D_PROJECTOR: 0.8,
                                 D_ALTAVEUS: 0.6},
    "Biblioteca": {D_WIFI: 1.8, D_SOBRETAULA: 1.2, D_IMPRESSORA: 1.4,
                   D_PORTATIL: 1.2, D_PROJECTOR: 0.2, D_ALTAVEUS: 0.3},
    "Administració": {D_IMPRESSORA: 2.2, D_SOBRETAULA: 1.4, D_PORTATIL: 1.3,
                      D_PANTALLA: 1.2, D_PROJECTOR: 0.1, D_ALTAVEUS: 0.3},
    "Sala de reunions": {D_PROJECTOR: 3.5, D_ALTAVEUS: 3.0, D_WIFI: 1.8,
                         D_PORTATIL: 1.4, D_SOBRETAULA: 0.4, D_IMPRESSORA: 0.2,
                         D_RATOLI: 0.4, D_TECLAT: 0.4},
    "Altres": {D_WIFI: 1.3, D_ALTRE: 3.0},
}

# Síntomas posibles (y su frecuencia) según el dispositivo.
SIMPTOMES = {
    D_SOBRETAULA: {S_NO_ENCEN: 16, S_LENT: 28, S_INTERNET: 14, S_PANTALLA: 10,
                   S_SO: 6, S_IMPRIMEIX: 5, S_TRENCADA: 5, S_NOSE: 5,
                   S_ALTRA: 11},
    D_PORTATIL: {S_NO_ENCEN: 14, S_LENT: 28, S_INTERNET: 18, S_PANTALLA: 8,
                 S_SO: 7, S_TRENCADA: 6, S_NOSE: 5, S_ALTRA: 14},
    D_PANTALLA: {S_PANTALLA: 55, S_NO_ENCEN: 18, S_TRENCADA: 12, S_NOSE: 5,
                 S_ALTRA: 10},
    D_TECLAT: {S_TRENCADA: 50, S_ALTRA: 35, S_NOSE: 15},
    D_RATOLI: {S_TRENCADA: 50, S_ALTRA: 35, S_NOSE: 15},
    D_IMPRESSORA: {S_IMPRIMEIX: 52, S_NO_ENCEN: 8, S_TRENCADA: 10, S_LENT: 6,
                   S_INTERNET: 10, S_ALTRA: 10, S_NOSE: 4},
    D_PROJECTOR: {S_PANTALLA: 34, S_NO_ENCEN: 25, S_SO: 12, S_TRENCADA: 6,
                  S_ALTRA: 15, S_NOSE: 8},
    D_WIFI: {S_INTERNET: 62, S_LENT: 25, S_ALTRA: 8, S_NOSE: 5},
    D_ALTAVEUS: {S_SO: 55, S_NO_ENCEN: 12, S_TRENCADA: 10, S_ALTRA: 15,
                 S_NOSE: 8},
    D_ALTRE: {S_NOSE: 25, S_ALTRA: 35, S_NO_ENCEN: 15, S_TRENCADA: 10,
              S_LENT: 15},
}

# P8: a quién afecta, según el dispositivo.
AFECTATS_BASE = {
    D_SOBRETAULA: {A_MI: 70, A_ALTRES: 20, A_TOTS: 5, A_NOSE: 5},
    D_PORTATIL: {A_MI: 78, A_ALTRES: 15, A_TOTS: 3, A_NOSE: 4},
    D_PANTALLA: {A_MI: 72, A_ALTRES: 18, A_TOTS: 5, A_NOSE: 5},
    D_TECLAT: {A_MI: 82, A_ALTRES: 12, A_TOTS: 2, A_NOSE: 4},
    D_RATOLI: {A_MI: 82, A_ALTRES: 12, A_TOTS: 2, A_NOSE: 4},
    D_IMPRESSORA: {A_MI: 28, A_ALTRES: 50, A_TOTS: 17, A_NOSE: 5},
    D_PROJECTOR: {A_MI: 15, A_ALTRES: 30, A_TOTS: 50, A_NOSE: 5},
    D_WIFI: {A_MI: 12, A_ALTRES: 38, A_TOTS: 45, A_NOSE: 5},
    D_ALTAVEUS: {A_MI: 25, A_ALTRES: 35, A_TOTS: 35, A_NOSE: 5},
    D_ALTRE: {A_MI: 50, A_ALTRES: 25, A_TOTS: 15, A_NOSE: 10},
}
# Multiplicadores por ubicación: (mult. "a altres", mult. "tota l'aula").
AFECTATS_LOC = {
    "Aula 101": (1.0, 1.5), "Aula 102": (1.0, 1.5),
    "Laboratori d'informàtica": (1.1, 1.5), "Biblioteca": (1.3, 1.0),
    "Administració": (1.4, 0.7), "Sala de reunions": (1.2, 1.4),
    "Altres": (1.0, 1.0),
}

# P7: si se puede seguir trabajando, según el síntoma (SI, DIF, NO, NOSE).
IMPACTE_BASE = {
    S_NO_ENCEN: (3, 12, 80, 5),
    S_LENT: (10, 78, 8, 4),
    S_INTERNET: (10, 35, 50, 5),
    S_PANTALLA: (20, 55, 20, 5),
    S_SO: (30, 40, 25, 5),
    S_IMPRIMEIX: (10, 30, 55, 5),
    S_TRENCADA: (30, 40, 25, 5),
    S_NOSE: (25, 35, 25, 15),
    S_ALTRA: (30, 45, 20, 5),
}

# P9: acciones previas, según el síntoma (reinicio, cables, internet,
# reprobar, nada, otra).
ACCIONS_BASE = {
    S_NO_ENCEN: (25, 45, 0, 8, 12, 10),
    S_LENT: (50, 2, 5, 22, 14, 7),
    S_INTERNET: (30, 10, 40, 10, 7, 3),
    S_PANTALLA: (12, 50, 0, 15, 15, 8),
    S_SO: (10, 35, 0, 20, 25, 10),
    S_IMPRIMEIX: (20, 28, 2, 28, 14, 8),
    S_TRENCADA: (2, 10, 0, 5, 75, 8),
    S_NOSE: (20, 15, 5, 20, 35, 5),
    S_ALTRA: (20, 15, 5, 25, 25, 10),
}
ACCIONS_ORDRE = [AC_REINICI, AC_CABLES, AC_INTERNET, AC_REPROVAR, AC_RES, AC_ALTRA]

# P10: probabilidad de que "ya hubiera pasado", según el síntoma.
REPETICIO_P_SI = {
    S_NO_ENCEN: 0.25, S_LENT: 0.55, S_INTERNET: 0.50, S_PANTALLA: 0.35,
    S_SO: 0.35, S_IMPRIMEIX: 0.50, S_TRENCADA: 0.08, S_NOSE: 0.30,
    S_ALTRA: 0.40,
}

# P11: otro equipo disponible, según el dispositivo (SI, PARCIAL, NO, NOSE).
ALTRE_EQUIP_BASE = {
    D_SOBRETAULA: (35, 35, 25, 5), D_PORTATIL: (30, 40, 25, 5),
    D_PANTALLA: (35, 35, 25, 5), D_TECLAT: (55, 25, 15, 5),
    D_RATOLI: (55, 25, 15, 5), D_IMPRESSORA: (35, 30, 30, 5),
    D_PROJECTOR: (10, 25, 60, 5), D_WIFI: (10, 20, 65, 5),
    D_ALTAVEUS: (20, 25, 50, 5), D_ALTRE: (20, 30, 40, 10),
}

# P12: mensaje de error, según el dispositivo (SI, NO, NOSE, SENSE PANTALLA).
ERROR_BASE = {
    D_SOBRETAULA: (40, 45, 12, 3), D_PORTATIL: (40, 45, 12, 3),
    D_PANTALLA: (30, 50, 15, 5), D_TECLAT: (3, 22, 10, 65),
    D_RATOLI: (3, 22, 10, 65), D_IMPRESSORA: (40, 40, 10, 10),
    D_PROJECTOR: (30, 35, 10, 25), D_WIFI: (35, 35, 10, 20),
    D_ALTAVEUS: (5, 25, 10, 60), D_ALTRE: (15, 35, 20, 30),
}
# Multiplicador de la probabilidad de "Si" según el síntoma.
ERROR_MULT_SIMPTOMA = {
    S_NO_ENCEN: 0.25, S_LENT: 0.8, S_INTERNET: 1.6, S_PANTALLA: 1.3,
    S_SO: 0.9, S_IMPRIMEIX: 1.5, S_TRENCADA: 0.15, S_NOSE: 0.5, S_ALTRA: 1.0,
}

# ============================================================================
# 4. TEXTOS: DESCRIPCIONES Y MENSAJES DE ERROR (en catalán, como el formulario)
# ============================================================================

# Nombre del equipo para las frases genéricas de ordenadores / otros equipos.
# {Eq} = principio de frase (mayúscula), {eq} = dentro de la frase.
EQUIP_NOM = {
    D_SOBRETAULA: ["l'ordinador de sobretaula", "l'ordinador"],
    D_PORTATIL: ["l'ordinador portàtil", "el portàtil"],
    D_ALTRE: ["l'equip"],
}

# Frases genéricas por síntoma (se usan con EQUIP_NOM).
DESC_GENERICA = {
    S_NO_ENCEN: [
        "{Eq} no s'encén quan premo el botó d'engegada.",
        "He intentat engegar {eq} però no dona cap senyal de vida.",
        "{Eq} s'ha apagat de cop i ara no torna a encendre's.",
        "No hi ha manera d'encendre {eq}, no s'encén cap llum.",
    ],
    S_LENT: [
        "{Eq} va molt lent, qualsevol programa triga molt a obrir-se.",
        "Des de fa un temps {eq} triga molt a respondre i es queda penjat sovint.",
        "Quan obro fitxers o navego per Internet, {eq} es bloqueja uns quants segons.",
        "{Eq} tarda uns quants minuts a arrencar i després va a batzegades.",
    ],
    S_INTERNET: [
        "{Eq} no té Internet, cap pàgina web no carrega.",
        "No puc accedir a Internet des de {eq}.",
        "El navegador de {eq} diu que no hi ha connexió i no puc consultar el correu.",
    ],
    S_PANTALLA: [
        "La pantalla de {eq} es veu malament, la imatge parpelleja.",
        "La imatge de {eq} surt amb ratlles i colors estranys.",
        "A la pantalla de {eq} es veu tot molt fosc i costa llegir.",
    ],
    S_SO: [
        "{Eq} no fa cap so, ni amb els altaveus ni amb els auriculars.",
        "No se sent res pel so de {eq}, tot i que el volum està pujat.",
    ],
    S_IMPRIMEIX: [
        "Des de {eq} no puc imprimir, la feina no surt per la impressora.",
        "{Eq} no troba la impressora i els documents queden a la cua.",
    ],
    S_TRENCADA: [
        "{Eq} té una peça trencada i no es pot fer servir amb normalitat.",
        "S'ha trencat una part de {eq}, sembla que ha rebut un cop.",
    ],
    S_NOSE: [
        "{Eq} no funciona bé però no sé identificar què li passa exactament.",
        "Passa alguna cosa estranya amb {eq} però no sé explicar què.",
    ],
    S_ALTRA: [
        "{Eq} es reinicia sol sense avisar i es tanquen els programes.",
        "{Eq} fa un soroll estrany i de tant en tant es congela la imatge.",
        "Les actualitzacions de {eq} no es completen i es queda a mitges.",
    ],
}

# Frases específicas por (dispositivo, síntoma). Tienen prioridad sobre las
# genéricas. Las que empiezan por un nombre propio (Wi-Fi) llevan artículo.
DESC_ESPECIFICA = {
    (D_PANTALLA, S_PANTALLA): [
        "La pantalla es veu molt fosca i amb ratlles verticals.",
        "La imatge de la pantalla parpelleja i té colors estranys.",
        "La pantalla es veu borrosa i no es pot llegir bé el text.",
    ],
    (D_PANTALLA, S_NO_ENCEN): [
        "La pantalla no s'encén, no mostra cap imatge ni cap llum.",
        "He premut el botó de la pantalla però no respon.",
    ],
    (D_PANTALLA, S_TRENCADA): [
        "La pantalla té el marc trencat i el suport no aguanta.",
        "La pantalla té una esquerda al vidre.",
    ],
    (D_PANTALLA, S_NOSE): [
        "La pantalla no va bé però no sé dir què li passa.",
    ],
    (D_PANTALLA, S_ALTRA): [
        "La pantalla es queda en negre uns segons i després torna sola.",
        "La pantalla canvia la mida de la imatge sense que jo toqui res.",
    ],
    (D_TECLAT, S_TRENCADA): [
        "Al teclat hi falten tecles i algunes no responen.",
        "El teclat té el cable trencat i es desconnecta sol.",
        "El teclat té una part de la carcassa trencada i les tecles no tornen a lloc.",
    ],
    (D_TECLAT, S_ALTRA): [
        "El teclat escriu lletres diferents de les que premo.",
        "El teclat escriu dues vegades la mateixa lletra.",
        "El teclat deixa de respondre cada cop que passen uns minuts.",
    ],
    (D_TECLAT, S_NOSE): [
        "El teclat no funciona bé i no sé per què.",
        "Hi ha tecles que van i altres no, no sé quin és el problema.",
    ],
    (D_RATOLI, S_TRENCADA): [
        "El ratolí té la rodeta trencada i no puc fer scroll.",
        "El ratolí té el botó esquerre trencat i no fa clic.",
        "El ratolí té el cable trencat per la part de dins.",
    ],
    (D_RATOLI, S_ALTRA): [
        "El cursor del ratolí es mou a batzegades per la pantalla.",
        "El ratolí fa doble clic quan només el premo una vegada.",
        "El ratolí de vegades deixa de respondre durant uns segons.",
    ],
    (D_RATOLI, S_NOSE): [
        "El ratolí no va bé però no sé identificar el motiu.",
    ],
    (D_IMPRESSORA, S_IMPRIMEIX): [
        "La impressora no imprimeix, els documents es queden a la cua.",
        "Envio documents a imprimir i no en surt cap full.",
        "La impressora treu els fulls en blanc.",
        "La impressora s'ha quedat sense respondre quan he enviat la feina.",
    ],
    (D_IMPRESSORA, S_NO_ENCEN): [
        "La impressora no s'encén, no mostra cap llum.",
        "He premut el botó de la impressora i no passa res.",
    ],
    (D_IMPRESSORA, S_TRENCADA): [
        "La impressora té la safata del paper trencada.",
        "La tapa de la impressora no tanca perquè hi ha una peça trencada.",
    ],
    (D_IMPRESSORA, S_LENT): [
        "La impressora triga molt a treure els fulls.",
        "Imprimir un document de poques pàgines porta uns quants minuts.",
    ],
    (D_IMPRESSORA, S_INTERNET): [
        "La impressora no es connecta a la xarxa i no apareix als ordinadors.",
        "Des dels ordinadors no es troba la impressora de la xarxa.",
    ],
    (D_IMPRESSORA, S_ALTRA): [
        "La impressora encalla el paper cada dos per tres.",
        "Les impressions surten amb ratlles i desdibuixades.",
    ],
    (D_IMPRESSORA, S_NOSE): [
        "La impressora fa coses estranyes però no sé explicar què.",
    ],
    (D_PROJECTOR, S_PANTALLA): [
        "La imatge del projector es veu borrosa i amb colors estranys.",
        "El projector mostra la imatge tallada i fosca a la paret.",
        "La imatge que projecta es veu amb una taca verda.",
    ],
    (D_PROJECTOR, S_NO_ENCEN): [
        "El projector no s'encén quan el posem en marxa.",
        "He premut el botó del projector però no s'encén cap llum.",
    ],
    (D_PROJECTOR, S_SO): [
        "No se sent el so del projector durant les presentacions.",
        "El projector no treu so, tot i que el volum està pujat.",
    ],
    (D_PROJECTOR, S_TRENCADA): [
        "El projector té una part de la carcassa trencada.",
        "El comandament del projector té la tapa de les piles trencada.",
    ],
    (D_PROJECTOR, S_ALTRA): [
        "El projector s'apaga sol al cap de poca estona.",
        "El projector fa un soroll fort de ventilador mentre està en marxa.",
    ],
    (D_PROJECTOR, S_NOSE): [
        "El projector no va bé però no sé dir què li passa.",
    ],
    (D_WIFI, S_INTERNET): [
        "No hi ha Wi-Fi, no es veu la xarxa al llistat.",
        "La xarxa Wi-Fi apareix però no hi ha accés a Internet.",
        "No em puc connectar al Wi-Fi i no carrega cap pàgina.",
    ],
    (D_WIFI, S_LENT): [
        "El Wi-Fi va molt lent, les pàgines triguen molt a carregar.",
        "Les videoconferències es tallen perquè el Wi-Fi va molt lent.",
        "La connexió Wi-Fi és molt lenta i descarregar un fitxer porta molta estona.",
    ],
    (D_WIFI, S_ALTRA): [
        "La connexió Wi-Fi es talla cada pocs minuts i s'ha de tornar a connectar.",
        "El Wi-Fi es desconnecta cada cop que canvio de lloc.",
    ],
    (D_WIFI, S_NOSE): [
        "El Wi-Fi no va bé però no sé identificar el problema.",
    ],
    (D_ALTAVEUS, S_SO): [
        "Els altaveus no fan cap so, tot i que estan connectats.",
        "El micròfon no capta la veu i ningú no ens sent a la reunió.",
        "No se sent res pels altaveus malgrat que el volum està pujat.",
    ],
    (D_ALTAVEUS, S_NO_ENCEN): [
        "Els altaveus no s'encenen, no es veu cap llum.",
        "El micròfon no s'encén quan premo el botó.",
    ],
    (D_ALTAVEUS, S_TRENCADA): [
        "Un dels altaveus té el con trencat i fa soroll.",
        "El micròfon té el cable trencat i no es pot fer servir.",
    ],
    (D_ALTAVEUS, S_ALTRA): [
        "Els altaveus fan un brunzit constant de fons.",
        "El micròfon fa retorn i xiula quan parlem.",
    ],
    (D_ALTAVEUS, S_NOSE): [
        "Els altaveus no van bé però no sé explicar què passa.",
    ],
}

# Detalles opcionales por dispositivo (se añaden a la descripción).
DETALLS = {
    D_SOBRETAULA: [
        "La torre fa un soroll estrany.",
        "El ventilador sembla que va a tota velocitat.",
        "Els cables semblen ben connectats a la part del darrere.",
        "L'ordinador és d'ús compartit i el fa servir molta gent.",
    ],
    D_PORTATIL: [
        "El carregador està connectat però la bateria no carrega.",
        "El portàtil s'escalfa molt.",
        "Ho he provat amb carregador i sense.",
        "És un portàtil d'ús compartit.",
    ],
    D_PANTALLA: [
        "El cable de vídeo està ben endollat.",
        "El problema s'accentua quan fa estona que està encesa.",
    ],
    D_TECLAT: ["És un teclat USB connectat directament a l'ordinador."],
    D_RATOLI: ["He provat de canviar-lo de port USB.", "Ho noto sobretot a primera hora."],
    D_IMPRESSORA: [
        "És la impressora compartida de la planta.",
        "Hi ha paper i tòner a la safata.",
        "El tòner sembla gairebé gastat.",
    ],
    D_PROJECTOR: [
        "El cable HDMI està ben connectat.",
        "El comandament a distància sembla que funciona.",
        "Ho necessito per a una classe.",
    ],
    D_WIFI: [
        "Passa tant amb el mòbil com amb l'ordinador.",
        "Només passa en algunes hores del dia.",
        "El senyal sembla molt feble en aquesta zona.",
    ],
    D_ALTAVEUS: [
        "El volum està al màxim i no estan silenciats.",
        "Els cables estan endollats a l'ordinador.",
    ],
    D_ALTRE: ["Es tracta d'un aparell que fem servir poc."],
}

# Detalles neutros (valen para cualquier síntoma) y síntomas con detalles de hardware.
DETALLS_NEUTRES = [
    "Abans funcionava sense cap problema.",
    "És un equip d'ús compartit.",
    "Ho he notat quan he arribat.",
]
DETALL_HARDWARE_SIMPTOMES = {S_NO_ENCEN, S_LENT, S_ALTRA, S_NOSE}

# Frases de ubicación (apertura opcional de la descripción).
LOC_FRASE = {
    "Aula 101": "A l'Aula 101",
    "Aula 102": "A l'Aula 102",
    "Laboratori d'informàtica": "Al laboratori d'informàtica",
    "Biblioteca": "A la biblioteca",
    "Administració": "A administració",
    "Sala de reunions": "A la sala de reunions",
    "Altres": "En un altre espai del centre",
}

# Frases coherentes con las respuestas estructuradas.
FRASE_INICI = {
    I_AHIR: ["El problema va començar ahir.", "Ahir ja va fallar."],
    I_DIES: ["Fa uns dies que passa això.", "Fa uns quants dies que va així."],
    I_SETMANA: ["Fa més d'una setmana que dura el problema.",
                "Això passa des de fa més d'una setmana."],
    I_NOSE: ["No sé exactament des de quan passa."],
}
FRASE_ACCIONS = {
    AC_REINICI: ["He apagat i tornat a encendre l'equip però el problema continua.",
                 "Reiniciar-lo no ha servit de res."],
    AC_CABLES: ["He revisat els cables i estan ben connectats.",
                "He comprovat tots els cables i sembla que estan bé."],
    AC_INTERNET: ["He comprovat si hi ha Internet però no ho he aconseguit resoldre.",
                  "He mirat la connexió a Internet i el problema segueix."],
    AC_REPROVAR: ["Ho he tornat a provar diverses vegades sense èxit.",
                  "He insistit uns quants cops però continua igual."],
    AC_RES: ["De moment no he provat res.", "Encara no he fet cap prova."],
    AC_ALTRA: ["He provat altres solucions però no han funcionat.",
               "He fet algunes proves pel meu compte sense resultat."],
}
FRASE_IMPACTE = {
    IMP_SI: ["De moment puc seguir treballant sense problemes."],
    IMP_DIF: ["Puc treballar però amb moltes dificultats.",
              "Puc continuar, però em costa bastant."],
    IMP_NO: ["No puc treballar amb aquest equip.",
             "Així no es pot fer la feina."],
}
FRASE_AFECTATS = {
    A_MI: ["Només em passa a mi."],
    A_ALTRES: ["També afecta altres companys."],
    A_TOTS: ["Afecta tota l'aula.", "Afecta tot el departament."],
}
FRASE_REPETICIO = {
    R_SI: ["Ja havia passat abans.", "Ja és la segona vegada que passa."],
    R_NO: ["És la primera vegada que passa."],
}
FRASE_ALTRE_EQUIP = {
    E_SI: ["Puc fer servir un altre equip mentrestant."],
    E_PARCIAL: ["Només puc fer servir un altre equip per a algunes tasques."],
    E_NO: ["No tinc cap altre equip disponible."],
}
FRASE_ERROR_SI = ["Apareix un missatge d'error a la pantalla.",
                  "A la pantalla surt un missatge d'error."]
FRASE_ERROR_NO = ["No apareix cap missatge d'error."]

# --- Mensajes de error (solo se usan cuando P12 = "Si") ----------------------
ERR_ORDINADOR = {
    S_NO_ENCEN: ["No bootable device", "Operating system not found",
                 "Windows no s'ha pogut iniciar correctament",
                 "Error de la BIOS: ventilador de la CPU no detectat",
                 "La reparació automàtica no ha pogut reparar l'equip"],
    S_LENT: ["Aquest programa no respon", "Memòria insuficient per executar l'aplicació",
             "L'espai del disc és gairebé ple", "El sistema ha deixat de respondre",
             "Not responding"],
    S_INTERNET: ["No hi ha connexió a Internet", "DNS_PROBE_FINISHED_NO_INTERNET",
                 "ERR_CONNECTION_TIMED_OUT", "No s'ha pogut resoldre l'adreça del servidor",
                 "ERR_NAME_NOT_RESOLVED", "Connectat, sense Internet"],
    S_PANTALLA: ["Resolució no suportada", "No signal", "Senyal d'entrada no detectat",
                 "El controlador de pantalla ha deixat de respondre i s'ha recuperat"],
    S_SO: ["No s'ha trobat cap dispositiu de sortida d'àudio",
           "Cap dispositiu de sortida seleccionat",
           "El servei d'àudio no s'està executant"],
    S_IMPRIMEIX: ["Impressora fora de línia", "No s'ha pogut connectar amb la impressora",
                  "La feina d'impressió ha fallat", "Error d'impressió 0x0000011b"],
    S_TRENCADA: ["Dispositiu USB no reconegut", "No s'ha detectat el teclat"],
    S_NOSE: ["S'ha produït un error inesperat", "Error desconegut", "Error 0x80070057"],
    S_ALTRA: ["S'ha produït un error inesperat", "L'aplicació s'ha tancat inesperadament",
              "El sistema ha trobat un problema i s'ha de reiniciar", "Error 0x80070057"],
}
ERR_PER_DISPOSITIU = {
    D_IMPRESSORA: ["Paper encallat", "Tòner baix", "Sense paper", "Impressora fora de línia",
                   "Error E-03", "Substitueix el cartutx de tòner", "Tapa oberta"],
    D_PROJECTOR: ["No hi ha senyal", "Sense senyal d'entrada HDMI",
                  "Temperatura elevada, apagant el projector", "Substitueix la làmpada",
                  "Check signal cable", "Senyal fora de rang"],
    D_WIFI: ["Sense accés a Internet", "No es pot connectar a aquesta xarxa",
             "Autenticació fallida", "S'ha produït un error en connectar a la xarxa Wi-Fi",
             "Obtenint adreça IP", "Connexió limitada"],
    D_PANTALLA: ["Senyal d'entrada no detectat", "No signal", "Resolució no suportada",
                 "Cable no connectat", "Fora de rang"],
    D_ALTAVEUS: ["No s'ha trobat cap dispositiu de sortida d'àudio",
                 "Dispositiu d'àudio no detectat", "Cap dispositiu de sortida seleccionat"],
    D_TECLAT: ["Dispositiu USB no reconegut", "No s'ha detectat el teclat",
               "El dispositiu USB no ha funcionat correctament"],
    D_RATOLI: ["Dispositiu USB no reconegut", "No s'ha detectat el ratolí",
               "El dispositiu USB no ha funcionat correctament"],
    D_ALTRE: ["S'ha produït un error inesperat", "Error desconegut",
              "Error de comunicació amb el dispositiu", "El dispositiu no respon"],
}

# --- Correos sintéticos (dominios reservados por la RFC 2606) ---------------
DOMINIS_EMAIL = {"example.com": 70, "example.org": 15, "example.net": 15}
PREFIXOS_EMAIL = {
    "Aula 101": {"docent": 70, "tic": 30},
    "Aula 102": {"docent": 70, "tic": 30},
    "Laboratori d'informàtica": {"docent": 65, "tic": 35},
    "Biblioteca": {"biblioteca": 60, "usuari": 40},
    "Administració": {"admin": 60, "gestio": 40},
    "Sala de reunions": {"coordinacio": 50, "docent": 50},
    "Altres": {"usuari": 100},
}
MAX_USUARIS_PER_PREFIX = 40

# --- Distribución temporal de los registros ---------------------------------
PES_HORES = {8: 8, 9: 12, 10: 12, 11: 11, 12: 8, 13: 5, 14: 6, 15: 10,
             16: 11, 17: 9, 18: 5, 19: 2}
PES_DIA_SETMANA = {0: 1.3, 1: 1.1, 2: 1.0, 3: 1.0, 4: 0.8}   # 0 = dilluns


# ============================================================================
# 5. UTILIDADES
# ============================================================================

class ErrorGenerador(Exception):
    """Error controlado del generador (se muestra sin traza)."""


def triar(rng: random.Random, pesos: dict):
    """Elige una clave de un diccionario {opcion: peso}."""
    claus = list(pesos)
    valors = [max(0.0, float(pesos[c])) for c in claus]
    if sum(valors) <= 0:
        raise ErrorGenerador("Se ha intentado elegir con todos los pesos a cero.")
    return rng.choices(claus, weights=valors, k=1)[0]


def amb_majuscula(text: str) -> str:
    return text[:1].upper() + text[1:]


def amb_minuscula(text: str) -> str:
    return text[:1].lower() + text[1:]


_SUBSTITUCIONS = {
    "\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"',
    "\u00ab": '"', "\u00bb": '"', "\u2013": "-", "\u2014": "-",
    "\u2026": "", "\u00a0": " ",
}


def netejar(text: str) -> str:
    """Normaliza un texto: NFC, comillas rectas, sin controles ni dobles espacios."""
    text = unicodedata.normalize("NFC", text)
    for origen, desti in _SUBSTITUCIONS.items():
        text = text.replace(origen, desti)
    text = "".join(c for c in text if unicodedata.category(c)[0] != "C")
    return re.sub(r"\s+", " ", text).strip()


def caracters_estranys(text: str) -> bool:
    """True si el texto contiene algo fuera del latín (+ extendido) o controles."""
    for c in text:
        if unicodedata.category(c)[0] == "C" or c == "\ufffd" or ord(c) > 0x24F:
            return True
    return False


def es_laborable(d: date) -> bool:
    return d.weekday() < 5


def enrere_laborables(d: date, n: int) -> date:
    """Retrocede n días laborables (lunes a viernes) desde d."""
    while n > 0:
        d -= timedelta(days=1)
        if es_laborable(d):
            n -= 1
    return d


def eslug_etiqueta_xml(text: str, index: int) -> str:
    """Convierte un encabezado de columna en un nombre de etiqueta XML válido."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()[:60]
    if not text:
        text = f"camp_{index + 1}"
    if not re.match(r"[a-z_]", text[0]):
        text = "c_" + text
    return text


# ============================================================================
# 6. GENERACIÓN DE UN REGISTRO
# ============================================================================

def comprovar_configuracio() -> None:
    """Comprueba que cada combinación (dispositivo, síntoma) tiene textos."""
    for disp, simptomes in SIMPTOMES.items():
        if disp not in DISPOSITIUS:
            raise ErrorGenerador(f"Dispositivo desconocido en SIMPTOMES: {disp}")
        for simp in simptomes:
            if not frases_base(disp, simp):
                raise ErrorGenerador(f"Faltan descripciones para ({disp}, {simp}).")
            if not pool_errors(disp, simp):
                raise ErrorGenerador(f"Faltan mensajes de error para ({disp}, {simp}).")
    for disp in DISPOSITIUS:
        if disp not in SIMPTOMES:
            raise ErrorGenerador(f"Falta SIMPTOMES para {disp}.")


def frases_base(disp: str, simp: str) -> list[str]:
    """Frases núcleo posibles para (dispositivo, síntoma)."""
    if (disp, simp) in DESC_ESPECIFICA:
        return DESC_ESPECIFICA[(disp, simp)]
    if disp in EQUIP_NOM:
        return DESC_GENERICA.get(simp, [])
    return []


def pool_errors(disp: str, simp: str) -> list[str]:
    if disp in (D_SOBRETAULA, D_PORTATIL):
        return ERR_ORDINADOR.get(simp) or ERR_ORDINADOR[S_ALTRA]
    return ERR_PER_DISPOSITIU.get(disp, [])


def data_incidencia(rng: random.Random, dia_registre: date, inici: str) -> date:
    """Fecha de la incidencia coherente con 'Quan va començar el problema?'."""
    if inici == I_AVUI:
        return dia_registre
    if inici == I_AHIR:
        return enrere_laborables(dia_registre, 1)
    if inici == I_DIES:
        return enrere_laborables(dia_registre, rng.randint(2, 4))
    if inici == I_SETMANA:
        return enrere_laborables(dia_registre, rng.randint(6, 20))   # >= 8 días naturales
    return enrere_laborables(dia_registre, rng.randint(0, 10))       # "No ho sé"


def generar_email(rng: random.Random, ubicacio: str) -> str:
    prefix = triar(rng, PREFIXOS_EMAIL[ubicacio])
    numero = rng.randint(1, MAX_USUARIS_PER_PREFIX)
    domini = triar(rng, DOMINIS_EMAIL)
    return f"{prefix}{numero:02d}@{domini}"


def construir_descripcio(rng: random.Random, r: dict, hora: int) -> str:
    """Compone una descripción libre coherente con el resto de respuestas."""
    disp, simp = r["dispositiu"], r["simptoma"]
    nucli = rng.choice(frases_base(disp, simp))
    if disp in EQUIP_NOM:
        eq = rng.choice(EQUIP_NOM[disp])
        nucli = nucli.format(Eq=amb_majuscula(eq), eq=eq)
        nucli = re.sub(r"\bde el\b", "del", nucli)      # "de el portàtil" -> "del portàtil"

    # Apertura con la ubicación (40 %).
    if rng.random() < 0.40:
        nucli = f"{LOC_FRASE[r['ubicacio']]}, {amb_minuscula(nucli)}"
    parts = [nucli]

    # 8 % de descripciones muy cortas: solo el núcleo.
    if rng.random() >= 0.08:
        if rng.random() < 0.45 and DETALLS.get(disp):
            pool = DETALLS[disp]
            # En ordenadores, los detalles de hardware solo encajan con ciertos síntomas.
            if disp in (D_SOBRETAULA, D_PORTATIL) and simp not in DETALL_HARDWARE_SIMPTOMES:
                pool = DETALLS_NEUTRES
            parts.append(rng.choice(pool))

        if rng.random() < 0.55:
            if r["inici"] == I_AVUI:
                parts.append("Ha passat avui mateix, " + (
                    "aquest matí." if hora < 14 else "aquesta tarda."))
            else:
                parts.append(rng.choice(FRASE_INICI[r["inici"]]))

        if r["hi_ha_error"] == M_SI and rng.random() < 0.40:
            parts.append(rng.choice(FRASE_ERROR_SI))
        elif r["hi_ha_error"] == M_NO and rng.random() < 0.10:
            parts.append(rng.choice(FRASE_ERROR_NO))

        if rng.random() < 0.55:
            parts.append(rng.choice(FRASE_ACCIONS[r["accions"]]))
        if rng.random() < 0.40 and r["impacte"] in FRASE_IMPACTE:
            parts.append(rng.choice(FRASE_IMPACTE[r["impacte"]]))
        if rng.random() < 0.30 and r["afectats"] in FRASE_AFECTATS:
            parts.append(rng.choice(FRASE_AFECTATS[r["afectats"]]))

        # Una de las dos: repetición u otro equipo.
        if rng.random() < 0.30:
            if rng.random() < 0.5 and r["repeticio"] in FRASE_REPETICIO:
                parts.append(rng.choice(FRASE_REPETICIO[r["repeticio"]]))
            elif r["altre_equip"] in FRASE_ALTRE_EQUIP:
                parts.append(rng.choice(FRASE_ALTRE_EQUIP[r["altre_equip"]]))

    return netejar(" ".join(parts))


def generar_registre(rng: random.Random, ts: datetime) -> dict:
    """Genera UN registro completo y coherente para la marca de tiempo ts."""
    r: dict = {"marca_temps": ts}

    # 1) Ubicación -> dispositivo -> síntoma
    ubicacio = triar(rng, UBICACIONS)
    pesos_disp = {d: w * LOC_DISP.get(ubicacio, {}).get(d, 1.0)
                  for d, w in DISPOSITIUS.items()}
    dispositiu = triar(rng, pesos_disp)
    simptoma = triar(rng, SIMPTOMES[dispositiu])
    r.update(ubicacio=ubicacio, dispositiu=dispositiu, simptoma=simptoma)

    # 2) Cuándo empezó (y la fecha de la incidencia derivada de ello)
    inici = triar(rng, OPCIONS_INICI)
    r["inici"] = inici
    r["data_incidencia"] = data_incidencia(rng, ts.date(), inici)

    # 3) A quién afecta
    pesos_af = dict(AFECTATS_BASE[dispositiu])
    m_altres, m_tots = AFECTATS_LOC[ubicacio]
    pesos_af[A_ALTRES] *= m_altres
    pesos_af[A_TOTS] *= m_tots
    afectats = triar(rng, pesos_af)
    r["afectats"] = afectats

    # 4) Impacto en el trabajo
    p_si, p_dif, p_no, p_nose = IMPACTE_BASE[simptoma]
    if afectats == A_TOTS:
        p_no *= 1.5
    elif afectats == A_MI:
        p_si *= 1.2
    r["impacte"] = triar(rng, {IMP_SI: p_si, IMP_DIF: p_dif, IMP_NO: p_no, IMP_NOSE: p_nose})

    # 5) Acciones previas
    pesos_ac = dict(zip(ACCIONS_ORDRE, ACCIONS_BASE[simptoma]))
    if inici == I_AVUI:
        pesos_ac[AC_RES] *= 1.8
    elif inici in (I_SETMANA, I_DIES):
        pesos_ac[AC_RES] *= 0.6
    if dispositiu not in (D_SOBRETAULA, D_PORTATIL, D_WIFI, D_ALTRE):
        pesos_ac[AC_INTERNET] *= 0.2
    r["accions"] = triar(rng, pesos_ac)

    # 6) ¿Había pasado antes?
    p = REPETICIO_P_SI[simptoma]
    if inici == I_SETMANA:
        p *= 1.4
    elif inici == I_AVUI:
        p *= 0.8
    p = min(p, 0.9)
    r["repeticio"] = triar(rng, {R_SI: p, R_NO: max(0.0, 0.93 - p), R_NOSE: 0.07})

    # 7) ¿Hay otro equipo disponible?
    p_si, p_par, p_no, p_nose = ALTRE_EQUIP_BASE[dispositiu]
    if ubicacio == "Laboratori d'informàtica":
        p_si *= 1.5
    if afectats == A_TOTS:
        p_no *= 1.4
    if r["impacte"] == IMP_SI:
        p_si *= 1.3
    r["altre_equip"] = triar(rng, {E_SI: p_si, E_PARCIAL: p_par, E_NO: p_no, E_NOSE: p_nose})

    # 8) Mensaje de error (texto solo si P12 = "Si")
    e_si, e_no, e_nose, e_sp = ERROR_BASE[dispositiu]
    e_si *= ERROR_MULT_SIMPTOMA[simptoma]
    hi_ha_error = triar(rng, {M_SI: e_si, M_NO: e_no, M_NOSE: e_nose,
                              M_SENSE_PANTALLA: e_sp})
    r["hi_ha_error"] = hi_ha_error
    r["text_error"] = (rng.choice(pool_errors(dispositiu, simptoma))
                       if hi_ha_error == M_SI else "")

    # 9) Correo sintético y descripción libre
    r["email"] = generar_email(rng, ubicacio)
    r["descripcio"] = construir_descripcio(rng, r, ts.hour)
    r["text_error"] = netejar(r["text_error"])
    return r


def validar_registre(r: dict) -> list[str]:
    """Devuelve la lista de problemas del registro (vacía = válido)."""
    problemes = []
    for camp, valids in OPCIONS_VALIDES.items():
        if r.get(camp) not in valids:
            problemes.append(f"{camp}: valor no válido {r.get(camp)!r}")
    if not re.fullmatch(r"[a-z]+\d{2}@example\.(com|org|net)", r["email"]):
        problemes.append("email no sintético")
    if r["data_incidencia"] > r["marca_temps"].date():
        problemes.append("la incidencia es posterior al registro")
    if (r["hi_ha_error"] == M_SI) != bool(r["text_error"]):
        problemes.append("texto de error incoherente con P12")
    if not r["descripcio"]:
        problemes.append("descripción vacía")
    for camp in ("email", "descripcio", "text_error"):
        if caracters_estranys(r[camp]) or "\n" in r[camp] or "\r" in r[camp]:
            problemes.append(f"{camp}: caracteres no permitidos")
    return problemes


# ============================================================================
# 7. GENERACIÓN DEL CONJUNTO COMPLETO
# ============================================================================

def generar_marques_de_temps(rng: random.Random, total: int, inici: date,
                             fi: date) -> list[datetime]:
    """Marcas de tiempo únicas (al segundo), en días laborables y ordenadas."""
    dies = [inici + timedelta(days=i) for i in range((fi - inici).days + 1)]
    dies = [d for d in dies if es_laborable(d)]
    if not dies:
        raise ErrorGenerador("El rango de fechas no contiene ningún día laborable.")
    pesos_dies = [PES_DIA_SETMANA[d.weekday()] for d in dies]
    ara = datetime.now()
    usades: set[datetime] = set()
    intents = 0
    maxim = total * 200 + 10000
    while len(usades) < total:
        intents += 1
        if intents > maxim:
            raise ErrorGenerador(
                "No hay suficientes marcas de tiempo distintas en el rango "
                "indicado. Amplía el rango con --inici / --fi.")
        d = rng.choices(dies, weights=pesos_dies, k=1)[0]
        h = triar(rng, PES_HORES)
        ts = datetime(d.year, d.month, d.day, h, rng.randint(0, 59), rng.randint(0, 59))
        if ts > ara:
            continue
        usades.add(ts)
    return sorted(usades)


def generar_incidencies(total: int, seed: int | None, inici: date,
                        fi: date) -> list[dict]:
    comprovar_configuracio()
    rng = random.Random(seed)
    marques = generar_marques_de_temps(rng, total, inici, fi)
    registres: list[dict] = []
    for ts in marques:
        for _ in range(50):                      # reintentos si la validación falla
            r = generar_registre(rng, ts)
            if not validar_registre(r):
                registres.append(r)
                break
        else:
            raise ErrorGenerador(f"No se pudo generar un registro válido para {ts}.")
    return registres


def a_fila(r: dict) -> list[str]:
    """Convierte un registro en la fila de texto del CSV (orden de CAMPS)."""
    fila = []
    for camp in CAMPS:
        v = r[camp]
        if camp == "marca_temps":
            v = v.strftime(FORMAT_MARCA_TEMPS)
        elif camp == "data_incidencia":
            v = v.strftime(FORMAT_DATA)
        fila.append(str(v))
    return fila


# ============================================================================
# 8. ESCRITURA DE FICHEROS (CSV / JSON / XML)
# ============================================================================

def escriure_atomic(ruta: Path, escriptor, copia_seguretat: bool) -> None:
    """Escribe en un temporal y lo mueve al destino (no deja ficheros a medias)."""
    tmp = ruta.with_name(ruta.name + ".tmp")
    try:
        escriptor(tmp)
        if copia_seguretat and ruta.exists():
            os.replace(ruta, ruta.with_name(ruta.name + ".bak"))
        os.replace(tmp, ruta)
    except OSError as exc:
        raise ErrorGenerador(f"No se pudo escribir '{ruta}': {exc}") from exc
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def escriure_csv(ruta: Path, capcaleres: list[str], files: list[list[str]],
                 bom: bool) -> None:
    def escriptor(tmp: Path) -> None:
        with open(tmp, "w", encoding="utf-8-sig" if bom else "utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
            w.writerow(capcaleres)
            w.writerows(files)
    return escriptor


def escriure_json(capcaleres: list[str], files: list[list[str]],
                  amb_id: bool):
    registres = []
    for fila in files:
        obj = dict(zip(capcaleres, fila))
        if amb_id:
            clau = "|".join(fila[:2] + fila[3:6])
            obj = {"id": hashlib.sha1(clau.encode("utf-8")).hexdigest()[:12], **obj}
        registres.append(obj)

    def escriptor(tmp: Path) -> None:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(registres, f, ensure_ascii=False, indent=2)
            f.write("\n")
    return escriptor


def escriure_xml(capcaleres: list[str], files: list[list[str]]):
    etiquetes: list[str] = []
    for i, cap in enumerate(capcaleres):
        et = eslug_etiqueta_xml(cap, i)
        while et in etiquetes:                  # evita etiquetas repetidas
            et += "_"
        etiquetes.append(et)

    def escriptor(tmp: Path) -> None:
        arrel = ET.Element("incidencies")
        for n, fila in enumerate(files, start=1):
            node = ET.SubElement(arrel, "incidencia", id=str(n))
            for et, valor in zip(etiquetes, fila):
                ET.SubElement(node, et).text = valor
        arbre = ET.ElementTree(arrel)
        if hasattr(ET, "indent"):               # Python >= 3.9
            ET.indent(arbre, space="  ")
        arbre.write(tmp, encoding="utf-8", xml_declaration=True)
    return escriptor


def verificar_fitxers(rutes: dict, capcaleres: list[str], total: int) -> list[str]:
    """Relee los ficheros escritos y comprueba que son correctos."""
    confirmacions = []
    if "csv" in rutes:
        with open(rutes["csv"], encoding="utf-8-sig", newline="") as f:
            files = list(csv.reader(f))
        if files[0] != capcaleres:
            raise ErrorGenerador("El CSV releído no tiene los encabezados esperados.")
        cos = files[1:]
        if len(cos) != total or any(len(x) != len(CAMPS) for x in cos):
            raise ErrorGenerador("El CSV releído no tiene el número esperado de filas/columnas.")
        if any(caracters_estranys(c) for fila in cos for c in fila):
            raise ErrorGenerador("El CSV contiene caracteres no permitidos.")
        confirmacions.append(f"CSV  : {len(cos)} filas x {len(CAMPS)} columnas verificadas")
    if "json" in rutes:
        with open(rutes["json"], encoding="utf-8") as f:
            dades = json.load(f)
        if len(dades) != total:
            raise ErrorGenerador("El JSON releído no tiene el número esperado de registros.")
        confirmacions.append(f"JSON : {len(dades)} registros verificados")
    if "xml" in rutes:
        n = len(ET.parse(rutes["xml"]).getroot())
        if n != total:
            raise ErrorGenerador("El XML releído no tiene el número esperado de registros.")
        confirmacions.append(f"XML  : {n} registros verificados")
    return confirmacions


# ============================================================================
# 9. RESUMEN FINAL
# ============================================================================

def estadistiques(registres: list[dict], files: list[list[str]]) -> dict:
    total = len(registres)
    amb_error = sum(1 for r in registres if r["hi_ha_error"] == M_SI)
    return {
        "total": total,
        "dispositius": Counter(r["dispositiu"] for r in registres),
        "ubicacions": Counter(r["ubicacio"] for r in registres),
        "simptomes": Counter(r["simptoma"] for r in registres),
        "pct_error": 100.0 * amb_error / total if total else 0.0,
        "amb_error": amb_error,
        "files_unics": len({tuple(f) for f in files}),
        # Ignorando marca de tiempo y correo: mide la variedad "real" del contenido.
        "contingut_unic": len({tuple(f[2:]) for f in files}),
        "descripcions_unies": len({f[6] for f in files}),
        "primera": min(r["marca_temps"] for r in registres),
        "ultima": max(r["marca_temps"] for r in registres),
    }


def _barra(pct: float, amplada: int = 24) -> str:
    return "█" * int(round(pct / 100 * amplada))


def mostrar_resum(est: dict, rutes: dict, confirmacions: list[str],
                  fets_backup: list[str]) -> None:
    total = est["total"]
    try:
        from rich.console import Console
        from rich.table import Table
        consola = Console()
        consola.print(f"\n[bold green]Incidencias generadas: {total}[/bold green]")
        for titol, clau in (("Distribución por dispositivo", "dispositius"),
                            ("Distribución por ubicación", "ubicacions"),
                            ("Distribución por síntoma", "simptomes")):
            t = Table(title=titol)
            t.add_column("Valor")
            t.add_column("Total", justify="right")
            t.add_column("%", justify="right")
            t.add_column("")
            for valor, n in est[clau].most_common():
                p = 100 * n / total
                t.add_row(valor, str(n), f"{p:.1f}", _barra(p))
            consola.print(t)
        consola.print(f"[bold]Con mensaje de error:[/bold] {est['amb_error']} "
                      f"({est['pct_error']:.1f} %)")
        imprimir = consola.print
    except ImportError:
        print(f"\nIncidencias generadas: {total}")
        for titol, clau in (("Distribución por dispositivo", "dispositius"),
                            ("Distribución por ubicación", "ubicacions"),
                            ("Distribución por síntoma", "simptomes")):
            print(f"\n{titol}")
            amplada = max(len(v) for v in est[clau])
            for valor, n in est[clau].most_common():
                p = 100 * n / total
                print(f"  {valor:<{amplada}}  {n:>5}  {p:5.1f} %  {_barra(p)}")
        print(f"\nCon mensaje de error: {est['amb_error']} ({est['pct_error']:.1f} %)")
        imprimir = print

    imprimir(f"Registros únicos (fila completa): {est['files_unics']} de {total}")
    imprimir(f"Registros únicos ignorando fecha/hora y correo: {est['contingut_unic']}")
    imprimir(f"Descripciones distintas: {est['descripcions_unies']}")
    imprimir(f"Periodo de registro: {est['primera']:%d/%m/%Y} - {est['ultima']:%d/%m/%Y}")
    imprimir("")
    for nom in fets_backup:
        imprimir(f"Copia de seguridad: {nom}")
    for c in confirmacions:
        imprimir(f"OK  {c}")
    for fmt, ruta in rutes.items():
        imprimir(f"Guardado: {ruta} ({ruta.stat().st_size / 1024:.0f} KB)")


# ============================================================================
# 10. LÍNEA DE COMANDOS
# ============================================================================

def parse_data(text: str) -> date:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"fecha no válida '{text}' (usa AAAA-MM-DD)")


def llegir_capcaleres(ruta: str) -> list[str]:
    """Lee la primera fila de un CSV real y la usa como encabezados."""
    try:
        with open(ruta, encoding="utf-8-sig", newline="") as f:
            fila = next(csv.reader(f), None)
    except OSError as exc:
        raise ErrorGenerador(f"No se pudo leer '{ruta}': {exc}") from exc
    if not fila or len(fila) != len(CAMPS):
        raise ErrorGenerador(
            f"'{ruta}' debe tener {len(CAMPS)} columnas en la primera fila "
            f"(tiene {0 if not fila else len(fila)}).")
    return [c.strip() for c in fila]


def crear_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Genera incidencias informáticas sintéticas para IncidentIQ.")
    p.add_argument("--total", type=int, default=TOTAL_PER_DEFECTE,
                   help=f"número de incidencias (por defecto {TOTAL_PER_DEFECTE})")
    p.add_argument("--seed", type=int, default=SEED_PER_DEFECTE,
                   help="semilla aleatoria; usa -1 para datos distintos en cada ejecución")
    p.add_argument("--formats", default="csv,json,xml",
                   help="formatos a crear separados por comas: csv,json,xml")
    p.add_argument("--sortida", default=".", help="directorio de salida")
    p.add_argument("--nom-base", default=NOM_BASE_PER_DEFECTE,
                   help="nombre de los ficheros sin extensión")
    p.add_argument("--inici", type=parse_data, help="primer día de registros (AAAA-MM-DD)")
    p.add_argument("--fi", type=parse_data, help="último día de registros (AAAA-MM-DD)")
    p.add_argument("--capcaleres", metavar="CSV",
                   help="CSV real cuya primera fila se usará como encabezados")
    p.add_argument("--bom", action="store_true",
                   help="escribe el CSV en UTF-8 con BOM (para abrirlo con Excel)")
    p.add_argument("--id-json", action="store_true",
                   help="añade un campo 'id' (hash) a cada registro del JSON")
    p.add_argument("--sense-copia", action="store_true",
                   help="no guardar copia .bak de los ficheros existentes")
    return p


def principal(argv: list[str] | None = None) -> int:
    args = crear_parser().parse_args(argv)

    # --- validación de argumentos ---------------------------------------
    if not 1 <= args.total <= 1_000_000:
        raise ErrorGenerador("--total debe estar entre 1 y 1.000.000.")
    formats = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
    if "csv" not in formats:
        formats.insert(0, "csv")                      # el CSV es obligatorio
    desconeguts = set(formats) - {"csv", "json", "xml"}
    if desconeguts:
        raise ErrorGenerador(f"Formato(s) no válido(s): {', '.join(sorted(desconeguts))}")
    fi = args.fi or (date.today() - timedelta(days=1))
    inici = args.inici or (fi - timedelta(days=DIES_INTERVAL_PER_DEFECTE))
    if inici > fi:
        raise ErrorGenerador("--inici no puede ser posterior a --fi.")
    seed = None if args.seed == -1 else args.seed
    capcaleres = (llegir_capcaleres(args.capcaleres) if args.capcaleres
                  else list(CAPCALERES_PER_DEFECTE))

    sortida = Path(args.sortida)
    try:
        sortida.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ErrorGenerador(f"No se pudo crear el directorio '{sortida}': {exc}") from exc

    # --- generación ------------------------------------------------------
    print(f"Generando {args.total} incidencias sintéticas...")
    registres = generar_incidencies(args.total, seed, inici, fi)
    files = [a_fila(r) for r in registres]

    # --- escritura -------------------------------------------------------
    copia = not args.sense_copia
    rutes = {fmt: sortida / f"{args.nom_base}.{fmt}" for fmt in formats}
    fets_backup = [f"{r.name}.bak" for r in rutes.values() if copia and r.exists()]
    escriure_atomic(rutes["csv"], escriure_csv(rutes["csv"], capcaleres, files, args.bom),
                    copia)
    if "json" in rutes:
        escriure_atomic(rutes["json"], escriure_json(capcaleres, files, args.id_json), copia)
    if "xml" in rutes:
        escriure_atomic(rutes["xml"], escriure_xml(capcaleres, files), copia)

    # --- verificación y resumen -----------------------------------------
    confirmacions = verificar_fitxers(rutes, capcaleres, args.total)
    mostrar_resum(estadistiques(registres, files), rutes, confirmacions, fets_backup)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(principal())
    except ErrorGenerador as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.", file=sys.stderr)
        sys.exit(130)
