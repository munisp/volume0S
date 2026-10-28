package os.volume.field.census

import org.junit.Assert.assertEquals
import org.junit.Test

class CensusRecordTest {
    private fun record() = CensusRecord("tenant-a", "device-1", "local-1", "consent-1", "consent", 6.5244, 3.3792, 8.0f, 1700000000, mapOf("business_name" to "Test Business"))
    @Test fun deterministicEventId() { assertEquals("48MMQRF017Y0HQ4C8QSQG8DD7Z", record().eventId); assertEquals(26, record().eventId.length) }
    @Test(expected = IllegalArgumentException::class) fun consentReferenceRequired() { record().copy(consentReference = "../bad") }
    @Test(expected = IllegalArgumentException::class) fun inaccurateLocationRejected() { record().copy(accuracyMeters = 150.0f) }
    @Test(expected = IllegalArgumentException::class) fun invalidLawfulBasisRejected() { record().copy(lawfulBasis = "unknown") }
}
