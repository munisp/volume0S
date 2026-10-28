package os.volume.field.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ChunkerTest {
    @Test fun chunksAreBoundedContentAddressedAndResumable() {
        val payload = ByteArray(CHUNK_SIZE * 2 + 17) { (it % 251).toByte() }
        val result = chunks(payload)
        assertEquals(3, result.size)
        assertEquals(0, result[0].index); assertEquals(1, result[1].index)
        assertTrue(result.all { it.digestHex.length == 64 })
        assertEquals(payload.toList(), result.flatMap { it.bytes.toList() })
    }
}
