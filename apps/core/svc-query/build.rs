fn main() -> Result<(), Box<dyn std::error::Error>> {
    tonic_build::compile_protos("proto/query.proto")?;
    println!("cargo:rerun-if-changed=proto/query.proto");
    Ok(())
}
