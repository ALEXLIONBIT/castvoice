package app.castvoice

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.core.content.ContextCompat
import kotlin.math.roundToInt

private val Scheme = darkColorScheme(
    primary = Color(0xFFA78BFA), onPrimary = Color(0xFF1B1030),
    primaryContainer = Color(0xFF3B2A78), onPrimaryContainer = Color(0xFFEDE7FF),
    secondary = Color(0xFF22D3EE), onSecondary = Color(0xFF00363D),
    background = Color(0xFF0F1420), onBackground = Color(0xFFE6E9F2),
    surface = Color(0xFF171D2E), onSurface = Color(0xFFE6E9F2),
    surfaceVariant = Color(0xFF222A40), onSurfaceVariant = Color(0xFFA7B0C8),
    outline = Color(0xFF44506E),
)

@Composable
fun CastVoiceTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = Scheme, content = content)
}

@Composable
fun MainScreen(vm: AppViewModel) {
    var tab by rememberSaveable { mutableIntStateOf(0) }
    Column(
        Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background)
            .systemBarsPadding()
            .imePadding(),
    ) {
        Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 8.dp, top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("CastVoice", fontSize = 22.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.primary, modifier = Modifier.weight(1f))
            TextButton(onClick = { vm.cast.rescan() }) { Text("⟳ Cerca") }
        }
        DevicePanel(vm)
        TabRow(selectedTabIndex = tab, containerColor = Color.Transparent) {
            listOf("💬 Parla", "🎵 Audio", "🎙 La mia voce").forEachIndexed { i, t ->
                Tab(selected = tab == i, onClick = { tab = i }, text = { Text(t, maxLines = 1, fontSize = 13.sp) })
            }
        }
        Box(Modifier.weight(1f)) {
            when (tab) {
                0 -> SpeakTab(vm)
                1 -> AudioTab(vm)
                else -> VoiceTab(vm)
            }
        }
        Text(
            vm.cast.status.ifEmpty { "Pronto." },
            Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surface).padding(horizontal = 16.dp, vertical = 8.dp),
            fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis,
        )
    }
}

@Composable
private fun DevicePanel(vm: AppViewModel) {
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp)) {
        if (vm.cast.devices.isEmpty()) {
            Text(
                "Cerco i Google sulla rete… (telefono e Google devono stare sullo stesso Wi-Fi)",
                fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        } else {
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                vm.cast.devices.forEach { d ->
                    val selected = d.id == vm.cast.selectedId
                    FilterChip(
                        selected = selected,
                        onClick = { if (selected) vm.cast.disconnect() else vm.cast.select(d) },
                        label = { Text(d.name) },
                    )
                }
            }
        }
        if (vm.cast.selectedId != null) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("🔈", fontSize = 18.sp)
                Slider(
                    value = vm.cast.volume,
                    onValueChange = { vm.cast.changeVolume(it) },
                    enabled = vm.cast.connected,
                    modifier = Modifier.weight(1f).padding(horizontal = 8.dp),
                )
                Text("${(vm.cast.volume * 100).roundToInt()}%", fontSize = 13.sp)
                TextButton(onClick = { vm.stopAll() }) { Text("⏹ Ferma") }
            }
        }
    }
}

// ============================================================================ PARLA
@Composable
private fun SpeakTab(vm: AppViewModel) {
    var pickerFor by rememberSaveable { mutableIntStateOf(-1) } // indice della parte di cui si sceglie la voce
    val parts = vm.parts
    val quick = remember(vm.voices, vm.favorites, vm.recents) {
        (vm.voices.filter { it.id in vm.favorites } + vm.recents.mapNotNull { id -> vm.voices.firstOrNull { it.id == id } })
            .distinctBy { it.id }.take(9)
    }
    val quickIds = quick.map { it.id }
    val lastFocus = remember { FocusRequester() }
    LaunchedEffect(parts.size) { if (parts.size > 1) runCatching { lastFocus.requestFocus() } }
    val muted = MaterialTheme.colorScheme.onSurfaceVariant

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(
            "Scrivi una parte, poi tocca un'altra voce e continua: nello stesso messaggio parleranno voci diverse.",
            fontSize = 12.sp, color = muted,
        )
        if (quick.isNotEmpty()) {
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                quick.forEachIndexed { i, v ->
                    FilterChip(
                        selected = parts.lastOrNull()?.voice == v.id,
                        onClick = { vm.continueWith(v.id) },
                        label = { Text("${i + 1} · ${v.title}") },
                        leadingIcon = { Box(Modifier.size(10.dp).background(partColor(v.id, quickIds), CircleShape)) },
                    )
                }
            }
        } else {
            Text("Scorciatoie: segna delle voci con ★ nella lista (o usale) e compariranno qui.", fontSize = 12.sp, color = muted)
        }
        parts.forEachIndexed { i, p ->
            val v = vm.voices.firstOrNull { it.id == p.voice }
            Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = MaterialTheme.shapes.medium, modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(Modifier.fillMaxWidth().clickable { pickerFor = i }, verticalAlignment = Alignment.CenterVertically) {
                        Box(Modifier.size(12.dp).background(partColor(p.voice, quickIds), CircleShape))
                        Spacer(Modifier.width(10.dp))
                        Column(Modifier.weight(1f)) {
                            Text(if (parts.size > 1) "Parte ${i + 1}" else "Voce", fontSize = 12.sp, color = muted)
                            Text(voiceLabel(v), fontWeight = FontWeight.Medium)
                        }
                        Text("Cambia ▸", color = MaterialTheme.colorScheme.primary)
                        if (parts.size > 1) TextButton(onClick = { vm.removePart(i) }) { Text("✕") }
                    }
                    OutlinedTextField(
                        value = p.text, onValueChange = { vm.editPart(i, it) },
                        label = { Text(if (i == 0) "Cosa vuoi dire?" else "…e poi?") },
                        minLines = if (parts.size > 1) 2 else 4, maxLines = 8,
                        modifier = Modifier.fillMaxWidth().then(if (i == parts.lastIndex) Modifier.focusRequester(lastFocus) else Modifier),
                    )
                }
            }
        }
        OutlinedButton(onClick = { vm.addPart() }, modifier = Modifier.fillMaxWidth()) { Text("＋ Aggiungi una parte con un'altra voce") }
        Column {
            Text("Velocità: ${if (vm.rate >= 0) "+" else ""}${vm.rate}%", fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Slider(value = vm.rate.toFloat(), onValueChange = { vm.changeRate(it.roundToInt()) }, valueRange = -50f..50f)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { vm.speak() }, enabled = !vm.busy, modifier = Modifier.weight(1f)) {
                Text(if (vm.busy) "…" else "▶ Parla")
            }
            OutlinedButton(onClick = { vm.savePhrase() }) { Text("💾 Salva") }
        }
        if (vm.busy) {
            Column {
                LinearProgressIndicator(progress = { vm.progress }, modifier = Modifier.fillMaxWidth())
                Text("${(vm.progress * 100).roundToInt()}% · ⏹ Ferma annulla", fontSize = 12.sp, color = muted)
            }
        }

        if (vm.phrases.isNotEmpty()) {
            HorizontalDivider(color = MaterialTheme.colorScheme.outline)
            Text("Frasi rapide", fontWeight = FontWeight.Medium)
            vm.phrases.toList().forEach { p ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    TextButton(
                        onClick = {
                            if (!vm.busy) vm.setParts(p.parts.ifEmpty { listOf(Part(p.voice, p.text)) })
                            vm.speak()
                        },
                        modifier = Modifier.weight(1f),
                    ) {
                        Text("▶ ${p.text}", maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.fillMaxWidth())
                    }
                    TextButton(onClick = { vm.deletePhrase(p) }) { Text("✕") }
                }
            }
        }
    }
    if (pickerFor >= 0) {
        VoicePicker(vm, selectedId = parts.getOrNull(pickerFor)?.voice ?: vm.voiceId, onPick = { vm.setPartVoice(pickerFor, it.id) }) {
            pickerFor = -1
        }
    }
}

private val PartColors = listOf(
    Color(0xFF22D3EE), Color(0xFFF59E0B), Color(0xFFA78BFA), Color(0xFF34D399), Color(0xFFF472B6),
    Color(0xFF60A5FA), Color(0xFFFB7185), Color(0xFFA3E635), Color(0xFFFBBF24),
)

/** Colore stabile di una voce: segue la posizione tra le scorciatoie, altrimenti l'id. */
private fun partColor(voiceId: String, order: List<String>): Color {
    val i = order.indexOf(voiceId)
    return PartColors[if (i >= 0) i % PartColors.size else kotlin.math.abs(voiceId.hashCode()) % PartColors.size]
}

private fun voiceLabel(v: Voice?): String =
    if (v == null) "Nessuna voce" else
        (if (v.online) "🌐 " else "💻 ") + v.title + " · ${langName(v.lang)}" + (if (v.note.isNotEmpty()) " · ${v.note}" else "")

@Composable
private fun VoicePicker(vm: AppViewModel, selectedId: String, onPick: (Voice) -> Unit, onClose: () -> Unit) {
    var type by rememberSaveable { mutableStateOf("all") }
    var query by rememberSaveable { mutableStateOf("") }
    val langs = remember(vm.voices) { vm.voices.map { it.lang }.distinct().sortedWith(compareBy({ it != "it" }, { it })) }
    var lang by rememberSaveable { mutableStateOf(vm.voices.firstOrNull { it.id == vm.voiceId }?.lang ?: "it") }
    val q = query.trim().lowercase()
    val shown = vm.voices.filter { v ->
        (type == "all" || (type == "fav" && v.id in vm.favorites) || (type == "offline" && !v.online) || (type == "online" && v.online)) &&
            (lang.isEmpty() || v.lang == lang) &&
            (q.isEmpty() || listOf(v.title, v.note, v.locale, langName(v.lang), if (v.online) "online" else "offline").any { it.lowercase().contains(q) })
    }.sortedBy { it.id !in vm.favorites }

    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false, decorFitsSystemWindows = true)) {
        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            Column(Modifier.systemBarsPadding().imePadding()) {
                Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 4.dp, top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("Scegli la voce", fontSize = 20.sp, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                    TextButton(onClick = onClose) { Text("Chiudi") }
                }
                Row(Modifier.horizontalScroll(rememberScrollState()).padding(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf("all" to "Tutte", "fav" to "★ Preferite", "online" to "🌐 Online", "offline" to "💻 Offline").forEach { (k, label) ->
                        FilterChip(selected = type == k, onClick = { type = k }, label = { Text(label) })
                    }
                }
                Spacer(Modifier.height(4.dp))
                Row(Modifier.horizontalScroll(rememberScrollState()).padding(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(selected = lang.isEmpty(), onClick = { lang = "" }, label = { Text("Tutte le lingue") })
                    langs.forEach { l ->
                        FilterChip(selected = lang == l, onClick = { lang = l }, label = { Text(langName(l)) })
                    }
                }
                OutlinedTextField(
                    value = query, onValueChange = { query = it },
                    label = { Text("Cerca (nome, online, offline…)") }, singleLine = true,
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
                )
                Text(
                    if (vm.voicesLoading) "Carico le voci…" else "${shown.size} voci · tocca per scegliere, 🔊 per ascoltare",
                    fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 2.dp),
                )
                LazyColumn(Modifier.weight(1f)) {
                    items(shown, key = { it.id }) { v ->
                        val selected = v.id == selectedId
                        Row(
                            Modifier.fillMaxWidth()
                                .background(if (selected) MaterialTheme.colorScheme.primaryContainer else Color.Transparent)
                                .clickable { onPick(v); onClose() }
                                .padding(start = 16.dp, top = 6.dp, bottom = 6.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(if (v.online) "🌐" else "💻", fontSize = 18.sp)
                            Spacer(Modifier.width(12.dp))
                            Column(Modifier.weight(1f)) {
                                Text(v.title, fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal)
                                Text(
                                    listOf(v.note, langName(v.lang), v.locale, if (v.online) "serve internet" else "senza internet")
                                        .filter { it.isNotEmpty() }.joinToString(" · "),
                                    fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            TextButton(onClick = { vm.preview(v) }) { Text(if (vm.previewingId == v.id) "⏹" else "🔊", fontSize = 18.sp) }
                            TextButton(onClick = { vm.toggleFavorite(v.id) }) {
                                Text(if (v.id in vm.favorites) "★" else "☆", fontSize = 20.sp, color = MaterialTheme.colorScheme.secondary)
                            }
                        }
                    }
                }
            }
        }
    }
}

// ============================================================================ AUDIO
@Composable
private fun AudioTab(vm: AppViewModel) {
    val pick = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris -> vm.addFiles(uris) }
    val files = vm.library.items.filter { it.kind == "file" }
    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Button(onClick = { pick.launch(arrayOf("audio/*", "video/*")) }, modifier = Modifier.fillMaxWidth()) {
            Text("＋ Aggiungi audio o video")
        }
        Text(
            "Dai video si sente solo l'audio. Tocca ▶ per riprodurre sul Google scelto.",
            fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        if (files.isEmpty()) {
            Text("Nessun file ancora.", color = MaterialTheme.colorScheme.onSurfaceVariant)
        } else {
            LazyColumn(Modifier.weight(1f)) {
                items(files, key = { it.id }) { item -> ItemRow(vm, item, clip = false) }
            }
        }
    }
}

@Composable
private fun ItemRow(vm: AppViewModel, item: Item, clip: Boolean) {
    Row(Modifier.fillMaxWidth().padding(vertical = 2.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(if (item.mime.startsWith("video/")) "🎬" else if (clip) "🎙" else "🎵", fontSize = 20.sp)
        Spacer(Modifier.width(10.dp))
        Text(item.name, Modifier.weight(1f), maxLines = 2, overflow = TextOverflow.Ellipsis)
        if (clip) TextButton(onClick = { vm.playClipHere(item) }) { Text("🔊") }
        TextButton(onClick = { vm.playItem(item) }, enabled = !vm.busy) { Text("▶", fontSize = 18.sp) }
        TextButton(onClick = { vm.remove(item) }) { Text("✕") }
    }
}

// ============================================================================ LA MIA VOCE
@Composable
private fun VoiceTab(vm: AppViewModel) {
    val ctx = LocalContext.current
    val mic = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        if (ok) vm.startRecording() else vm.say("Serve il permesso del microfono per registrare.")
    }
    val clips = vm.library.items.filter { it.kind == "clip" }
    var name by rememberSaveable { mutableStateOf("") }

    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(
            "Registra la tua voce e riproducila sui Google quando vuoi. Le registrazioni restano solo su questo telefono.",
            fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Button(
            onClick = {
                if (vm.recording) vm.stopRecording()
                else if (ContextCompat.checkSelfPermission(ctx, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) vm.startRecording()
                else mic.launch(Manifest.permission.RECORD_AUDIO)
            },
            colors = if (vm.recording) ButtonDefaults.buttonColors(containerColor = Color(0xFFDC2626), contentColor = Color.White) else ButtonDefaults.buttonColors(),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(if (vm.recording) "⏹ Ferma  (%d:%02d)".format(vm.recordSeconds / 60, vm.recordSeconds % 60) else "🎙 Registra")
        }
        Text(
            "Parlare con la tua voce un testo qualsiasi (clonazione) funziona solo sul PC, con l'app desktop.",
            fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        if (clips.isNotEmpty()) {
            HorizontalDivider(color = MaterialTheme.colorScheme.outline)
            LazyColumn(Modifier.weight(1f)) { items(clips, key = { it.id }) { ItemRow(vm, it, clip = true) } }
        }
    }

    if (vm.pendingClip != null) {
        AlertDialog(
            onDismissRequest = { vm.discardClip() },
            title = { Text("Salva la registrazione") },
            text = {
                OutlinedTextField(value = name, onValueChange = { name = it }, label = { Text("Nome") }, singleLine = true)
            },
            confirmButton = { TextButton(onClick = { vm.saveClip(name); name = "" }) { Text("Salva") } },
            dismissButton = { TextButton(onClick = { vm.discardClip(); name = "" }) { Text("Scarta") } },
        )
    }
}
