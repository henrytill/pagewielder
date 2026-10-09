{
  description = "A tool for manipulating PDFs";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    flake-compat = {
      url = "github:edolstra/flake-compat";
      flake = false;
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      ...
    }:
    let
      baseVersion = nixpkgs.lib.trim (builtins.readFile ./VERSION);
      gitRef = self.shortRev or self.dirtyShortRev;
      version = "${baseVersion}+${gitRef}";
      makePagewielder =
        pkgs:
        pkgs.python3Packages.buildPythonApplication {
          pname = "pagewielder";
          inherit version;
          pyproject = true;
          build-system = with pkgs.python3Packages; [ hatchling ];
          dependencies = with pkgs.python3Packages; [ pikepdf ];
          nativeCheckInputs = with pkgs.python3Packages; [ mypy ];
          src = self;
          patchPhase = "patchShebangs run.py";
          # version.py asks git for the reference, and the sandbox has no .git.
          env.PAGEWIELDER_GIT_REF = gitRef;
          checkPhase = "./run.py check";
        };
    in
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
      in
      {
        packages.pagewielder = makePagewielder pkgs;
        packages.default = self.packages.${system}.pagewielder;
      }
    );
}
