/**
 * Xsell – Datos de la web (Google Analytics) para el dashboard de GitHub.
 * Proyecto de Apps Script en la cuenta fabian02032000@gmail.com.
 * Servicios avanzados activados: Google Analytics Data API (AnalyticsData)
 * y Google Analytics Admin API (AnalyticsAdmin).
 *
 * 1. Ejecuta "configurar" una vez (pide permisos y deja la actualización cada hora).
 * 2. El script deja los números en la hoja "Xsell – Datos Web (dashboard)",
 *    que el dashboard descarga sola.
 */
var MEDICION = 'G-9308K1YS8Q';
var NOMBRE_HOJA = 'Xsell – Datos Web (dashboard)';
var TROZO = 40000;
var EVENTOS = ['page_view', 'diagnostico_abierto', 'diag_paso_0_inicio', 'diag_paso_1_tu_negocio', 'diag_paso_2_tus_numeros',
  'diag_paso_3_tu_reto', 'diag_paso_4_resultado', 'diag_paso_5_datos_contacto', 'generate_lead', 'clic_whatsapp'];

function configurar() {
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('actualizar').timeBased().everyHours(1).create();
  actualizar();
}

function actualizar() {
  var prop = propiedad();
  var desde = '120daysAgo', hasta = 'today';
  var datos = {
    updated_at: new Date().toISOString(),
    medicion: MEDICION,
    dia: reporte(prop, desde, hasta, ['date'], ['sessions', 'totalUsers', 'newUsers', 'engagedSessions', 'userEngagementDuration', 'screenPageViews']),
    origen: reporte(prop, desde, hasta, ['date', 'sessionSource', 'sessionMedium', 'sessionCampaignName'], ['sessions', 'totalUsers', 'engagedSessions', 'userEngagementDuration']),
    lugar: reporte(prop, desde, hasta, ['date', 'country', 'region', 'city'], ['sessions', 'totalUsers', 'userEngagementDuration']),
    eventos: reporte(prop, desde, hasta, ['date', 'eventName'], ['eventCount', 'totalUsers'], EVENTOS),
    eventos_origen: reporte(prop, desde, hasta, ['date', 'eventName', 'sessionSource', 'sessionMedium'], ['eventCount'], EVENTOS),
    paginas: reporte(prop, desde, hasta, ['date', 'landingPage'], ['sessions', 'engagedSessions', 'userEngagementDuration']),
    dispositivo: reporte(prop, desde, hasta, ['date', 'deviceCategory'], ['sessions', 'userEngagementDuration']),
    hora: reporte(prop, desde, hasta, ['date', 'hour'], ['sessions'])
  };
  var ss = abrirHoja();
  var hoja = ss.getSheets()[0];
  hoja.setName('json');
  hoja.clearContents();
  var texto = JSON.stringify(datos), filas = [];
  for (var i = 0; i < texto.length; i += TROZO) filas.push([texto.substring(i, i + TROZO)]);
  hoja.getRange(1, 1, filas.length, 1).setNumberFormat('@').setValues(filas);
  Logger.log('HOJA ID: ' + ss.getId() + ' | propiedad: ' + prop + ' | trozos: ' + filas.length);
}

// Busca la propiedad de Analytics que tiene el ID de medición de la web.
function propiedad() {
  var guardada = PropertiesService.getScriptProperties().getProperty('PROPIEDAD');
  if (guardada) return guardada;
  var cuentas = AnalyticsAdmin.AccountSummaries.list({ pageSize: 200 }).accountSummaries || [];
  for (var a = 0; a < cuentas.length; a++) {
    var props = cuentas[a].propertySummaries || [];
    for (var p = 0; p < props.length; p++) {
      var flujos = AnalyticsAdmin.Properties.DataStreams.list(props[p].property).dataStreams || [];
      for (var f = 0; f < flujos.length; f++) {
        if (flujos[f].webStreamData && flujos[f].webStreamData.measurementId === MEDICION) {
          PropertiesService.getScriptProperties().setProperty('PROPIEDAD', props[p].property);
          return props[p].property;
        }
      }
    }
  }
  throw new Error('No encontré la propiedad de Analytics con ' + MEDICION);
}

function reporte(prop, desde, hasta, dims, mets, soloEventos) {
  var pedido = {
    dateRanges: [{ startDate: desde, endDate: hasta }],
    dimensions: dims.map(function (d) { return { name: d }; }),
    metrics: mets.map(function (m) { return { name: m }; }),
    limit: 100000
  };
  if (soloEventos) pedido.dimensionFilter = { filter: { fieldName: 'eventName', inListFilter: { values: soloEventos } } };
  var res = AnalyticsData.Properties.runReport(pedido, prop);
  return (res.rows || []).map(function (r) {
    var fila = r.dimensionValues.map(function (v, i) {
      var x = v.value;
      return dims[i] === 'date' ? x.substr(0, 4) + '-' + x.substr(4, 2) + '-' + x.substr(6, 2) : x;
    });
    r.metricValues.forEach(function (v) { fila.push(Math.round(Number(v.value) * 100) / 100); });
    return fila;
  });
}

function abrirHoja() {
  var archivos = DriveApp.getFilesByName(NOMBRE_HOJA);
  var ss = archivos.hasNext() ? SpreadsheetApp.openById(archivos.next().getId()) : SpreadsheetApp.create(NOMBRE_HOJA);
  DriveApp.getFileById(ss.getId()).setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
  return ss;
}
