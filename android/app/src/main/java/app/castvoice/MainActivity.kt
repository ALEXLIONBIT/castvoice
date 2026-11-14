package app.castvoice

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.core.content.ContextCompat

class MainActivity : ComponentActivity() {
    private val vm: AppViewModel by viewModels()

    // Per trovare i Google sulla rete locale Android 13+ chiede "dispositivi nelle vicinanze".
    private val nearby = registerForActivityResult(ActivityResultContracts.RequestPermission()) { vm.cast.start() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent { CastVoiceTheme { MainScreen(vm) } }
    }

    override fun onStart() {
        super.onStart()
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.NEARBY_WIFI_DEVICES) != PackageManager.PERMISSION_GRANTED
        ) {
            nearby.launch(Manifest.permission.NEARBY_WIFI_DEVICES)
        } else {
            vm.cast.start()
        }
    }

    override fun onStop() {
        vm.cast.stopScan()
        super.onStop()
    }
}
