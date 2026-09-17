let sessionId = null;
let puntos = []; // Array para almacenar puntos editables
let puntosMovidosManual = 0; // Contador de puntos movidos manualmente

// =====================
// 🗺️ MAPA
// =====================
var map = L.map('map').setView([19.0414, -98.2063], 13);

L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
}).addTo(map);

let capaBuenos = L.layerGroup().addTo(map);
let capaMalos = L.layerGroup().addTo(map);

// =====================
// 📤 SUBIR ARCHIVOS
// =====================
function subir() {
    let inputBueno = document.getElementById("bueno");
    let inputMalo = document.getElementById("malo");

    if (inputBueno.files.length === 0 || inputMalo.files.length === 0) {
        alert("❌ Debes seleccionar ambos archivos KMZ");
        return;
    }

    let formData = new FormData();
    formData.append("bueno", inputBueno.files[0]);
    formData.append("malo", inputMalo.files[0]);

    // Resetear contadores y puntos
    puntosMovidosManual = 0;
    if (puntos.length > 0) {
        puntos.forEach(p => {
            if (p.marker) map.removeLayer(p.marker);
        });
        puntos = [];
    }

    fetch("/upload", {
        method: "POST",
        body: formData
    })
    .then(res => {
        if (!res.ok) throw new Error("Error en upload");
        return res.json();
    })
    .then(data => {
        sessionId = data.id;

        capaBuenos.clearLayers();
        capaMalos.clearLayers();

        let bounds = [];

        // 🟢 PUNTOS BUENOS
        data.buenos.forEach(p => {
            let marker = L.circleMarker([p[1], p[0]], {
                radius: 5,
                color: "green",
                weight: 2,
                fillColor: "green",
                fillOpacity: 0.5
            }).addTo(capaBuenos);
            bounds.push([p[1], p[0]]);
        });

        // 🔴 PUNTOS MALOS (solo visualización inicial)
        data.malos.forEach(p => {
            let marker = L.circleMarker([p[1], p[0]], {
                radius: 5,
                color: "red",
                weight: 2,
                fillColor: "red",
                fillOpacity: 0.5
            }).addTo(capaMalos);
            bounds.push([p[1], p[0]]);
        });

        if (bounds.length > 0) {
            map.fitBounds(bounds);
        }

        document.getElementById("resumen").innerHTML = "Archivos cargados. Listo para corregir.";
    })
    .catch(err => {
        console.error(err);
        alert("❌ Error al subir archivos");
    });
}

// =====================
// ⚙️ CORREGIR PUNTOS
// =====================
function corregir() {
    if (!sessionId) {
        alert("❌ Primero carga archivos");
        return;
    }

    fetch(`/corregir/${sessionId}`)
    .then(res => res.json())
    .then(data => {
        capaMalos.clearLayers();
        
        // Limpiar markers anteriores
        puntos.forEach(p => {
            if (p.marker) map.removeLayer(p.marker);
        });
        puntos = [];
        puntosMovidosManual = 0; // Resetear contador

        let corregidos = 0;
        let ignorados = 0;

        data.resultado.forEach((p, index) => {
            let lat = p.coord[1];
            let lon = p.coord[0];

            // 🟢 PUNTOS CORREGIDOS AUTOMÁTICAMENTE
            if (p.status === "corregido") {
                L.circleMarker([lat, lon], {
                    radius: 6,
                    color: "green",
                    weight: 2,
                    fillColor: "lightgreen",
                    fillOpacity: 0.7
                }).addTo(capaMalos);
                corregidos++;
            }
            // 🔴 PUNTOS NO CORREGIDOS (EDITABLES)
            else {
                let circle = L.circleMarker([lat, lon], {
                    radius: 6,
                    color: "red",
                    weight: 2,
                    fillColor: "red",
                    fillOpacity: 0.7
                }).addTo(map);
                
                // Variables para arrastre
                let isDragging = false;
                
                circle.on('mousedown', function() {
                    isDragging = true;
                    map.dragging.disable();
                });
                
                map.on('mousemove', function(e) {
                    if (isDragging) {
                        circle.setLatLng(e.latlng);
                    }
                });
                
                map.on('mouseup', function(e) {
                    if (isDragging) {
                        isDragging = false;
                        map.dragging.enable();
                        
                        // Actualizar coordenadas
                        let newPos = circle.getLatLng();
                        let punto = puntos.find(x => x.marker === circle);
                        if (punto) {
                            punto.coord = [newPos.lng, newPos.lat];
                            guardarCambios();
                        }
                    }
                });
                
                circle.bindPopup("🔴 Arrástrame (mantén clic)");
                
                puntos.push({
                    index: index,
                    marker: circle,
                    coord: [lon, lat],
                    originalCoord: [lon, lat],
                    fueMovido: false
                });
                
                ignorados++;
            }
        });

        let total = corregidos + ignorados;
        let porcentaje = total > 0 ? ((corregidos / total) * 100).toFixed(1) : 0;

        document.getElementById("resumen").innerHTML = `
            <b>Total puntos:</b> ${total} <br>
            <b>Corregidos automáticos:</b> ${corregidos} 🟢 <br>
            <b>No corregidos (editables):</b> ${ignorados} 🔴 <br>
            <b>% Corrección:</b> ${porcentaje}% <br>
            <b>✏️ Puntos movidos manualmente:</b> ${puntosMovidosManual} 🔵
        `;

        alert("✅ Ahora puedes mover los puntos rojos (mantén clic y arrastra)");
    })
    .catch(err => {
        console.error(err);
        alert("❌ Error al corregir");
    });
}

// =====================
// 💾 GUARDAR CAMBIOS
// =====================
function guardarCambios() {
    if (!sessionId || puntos.length === 0) {
        return;
    }

    let datosParaServidor = puntos.map(p => ({
        index: p.index,
        coord: p.coord
    }));

    // Contar puntos movidos manualmente
    let movidos = 0;
    puntos.forEach(punto => {
        if (!punto.originalCoord) {
            punto.originalCoord = [...punto.coord];
        } else if (punto.coord[0] !== punto.originalCoord[0] || 
                   punto.coord[1] !== punto.originalCoord[1]) {
            if (!punto.fueMovido) {
                punto.fueMovido = true;
                puntosMovidosManual++;
                movidos++;
            }
        }
    });
    
    if (movidos > 0) {
        actualizarResumenManual();
    }

    fetch(`/guardarCambios/${sessionId}`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify(datosParaServidor)
    })
    .then(res => res.json())
    .then(data => {
        if (movidos > 0) {
            console.log(`✅ ${movidos} punto(s) movido(s) manualmente`);
        }
    })
    .catch(err => {
        console.error("Error guardando:", err);
        alert("❌ Error al guardar cambios");
    });
}

// =====================
// 📊 ACTUALIZAR RESUMEN
// =====================
function actualizarResumenManual() {
    let resumenDiv = document.getElementById("resumen");
    let contenidoActual = resumenDiv.innerHTML;
    
    // Buscar si ya existe la línea de movimientos manuales
    if (contenidoActual.includes("Puntos movidos manualmente:")) {
        // Reemplazar la línea existente
        let lines = contenidoActual.split('<br>');
        for (let i = 0; i < lines.length; i++) {
            if (lines[i].includes("Puntos movidos manualmente:")) {
                lines[i] = `<b>✏️ Puntos movidos manualmente:</b> ${puntosMovidosManual} 🔵`;
                break;
            }
        }
        resumenDiv.innerHTML = lines.join('<br>');
    } else {
        // Agregar nueva línea
        resumenDiv.innerHTML = contenidoActual + `<br><b>✏️ Puntos movidos manualmente:</b> ${puntosMovidosManual} 🔵`;
    }
}

// =====================
// 💾 DESCARGAR KMZ
// =====================
function descargar() {
    if (!sessionId) {
        alert("❌ Primero carga y corrige");
        return;
    }

    let nombre = document.getElementById("nombre").value || "corregido";
    window.location = `/descargar/${sessionId}?nombre=${nombre}`;
}

// ==========================================
// 🔢 FUNCIÓN SEPARADA: RENUMERAR INDEPENDIENTE
// ==========================================
function procesarRenumeracionSolo() {
    let input = document.getElementById("archivo_renumerar");
    if (input.files.length === 0) {
        alert("❌ Por favor selecciona un archivo KMZ o KML");
        return;
    }

    let formData = new FormData();
    formData.append("archivo_kmz", input.files[0]);

    let resumenDiv = document.getElementById("resumen_renumerar");
    resumenDiv.innerHTML = "Procesando...";

    fetch("/renumerar_solo", {
        method: "POST",
        body: formData
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === "ok") {
            resumenDiv.innerHTML = `✅ <b>Completado!</b> Se re-enumeraron <b>${data.postes}</b> postes.`;
            
            // Descargar inmediatamente el archivo procesado
            let nombreSalida = "renumerado_" + input.files[0].name.replace(/\.[^/.]+$/, "");
            window.location = `/descargar/${data.id}?nombre=${nombreSalida}`;
        } else {
            alert("❌ Error: " + (data.error || "No se pudo procesar"));
            resumenDiv.innerHTML = "❌ Error en el proceso";
        }
    })
    .catch(err => {
        console.error(err);
        alert("❌ Error al procesar archivo");
        resumenDiv.innerHTML = "❌ Error de conexión";
    });
}