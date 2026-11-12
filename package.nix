{
  lib,
  stdenv,
  stdenvNoCC,
  fetchurl,
  appimageTools,
  makeWrapper,
}:

let
  sources = builtins.fromJSON (builtins.readFile ./sources.json);
  system = stdenv.hostPlatform.system;
  source = sources.${system} or (throw "Unsupported platform: ${system}");
  pname = "github-copilot-desktop";
  inherit (sources) version;
  src = fetchurl {
    url = "https://github.com/github/app/releases/download/${sources.tag}/${source.name}";
    inherit (source) hash;
  };
  meta = {
    description = "GitHub Copilot desktop app";
    homepage = "https://github.com/github/app";
    changelog = "https://github.com/github/app/releases/tag/${sources.tag}";
    license = lib.licenses.unfree;
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
    mainProgram = pname;
    platforms = [
      "x86_64-linux"
      "aarch64-linux"
      "x86_64-darwin"
      "aarch64-darwin"
    ];
  };
in
if stdenv.hostPlatform.isLinux then
  let
    appimageContents = appimageTools.extract {
      inherit pname version src;
    };
  in
  appimageTools.wrapType2 {
    inherit
      pname
      version
      src
      meta
      ;

    # Keep upstream's bundled WebKit, native helpers and AppRun hooks together.
    # An FHS environment also lets the bundled agent launch ordinary Linux tools.
    extraPkgs = p: [
      p.cacert
      p.git
      p.gh
      p.openssh
    ];

    extraInstallCommands = ''
      install -Dm644 \
        "${appimageContents}/usr/share/applications/GitHub Copilot.desktop" \
        "$out/share/applications/github-copilot-desktop.desktop"
      substituteInPlace "$out/share/applications/github-copilot-desktop.desktop" \
        --replace-fail 'Exec=github %u' "Exec=$out/bin/${pname} %u" \
        --replace-fail 'Icon=github' 'Icon=github-copilot-desktop' \
        --replace-fail 'Categories=' 'Categories=Development;'
      install -Dm644 "${appimageContents}/GitHub Copilot.png" \
        "$out/share/icons/hicolor/256x256/apps/github-copilot-desktop.png"
    '';

    passthru = {
      inherit appimageContents;
    };
  }
else if stdenv.hostPlatform.isDarwin then
  stdenvNoCC.mkDerivation {
    inherit
      pname
      version
      src
      meta
      ;
    nativeBuildInputs = [ makeWrapper ];
    sourceRoot = ".";
    dontConfigure = true;
    dontBuild = true;

    # Do not rewrite Mach-O paths or strip signed upstream binaries.
    dontFixup = true;
    installPhase = ''
      runHook preInstall
      mkdir -p "$out/Applications" "$out/bin"
      cp -R "GitHub Copilot.app" "$out/Applications/"
      makeWrapper "$out/Applications/GitHub Copilot.app/Contents/MacOS/github" \
        "$out/bin/${pname}"
      runHook postInstall
    '';
  }
else
  throw "Unsupported platform: ${system}"
