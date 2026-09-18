import Foundation
import CryptoKit

// Verification only needs the public key; never request the private Keychain key.
let args = CommandLine.arguments
let bytes = try Data(contentsOf: URL(fileURLWithPath: args[1]))
guard let publicBytes = Data(base64Encoded: args[2]), let signature = Data(base64Encoded: args[3]) else {
    fputs("Invalid update signature encoding\n", stderr); exit(1)
}
let key = try Curve25519.Signing.PublicKey(rawRepresentation: publicBytes)
let length = args.count > 4 ? Int(args[4])! : bytes.count
guard length > 0 && length <= bytes.count && key.isValidSignature(signature, for: bytes.prefix(length)) else {
    fputs("Update signature verification failed\n", stderr); exit(1)
}
print("Update signature verified")
