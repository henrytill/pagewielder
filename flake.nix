{
  description = "A password manager";

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
      makePagewielder =
        pkgs:
        pkgs.python3Packages.buildPythonApplication {
          name = "pagewielder";
          pyproject = true;
          build-system = with pkgs.python3Packages; [ setuptools ];
          dependencies = with pkgs.python3Packages; [ pikepdf ];
          nativeCheckInputs = with pkgs.python3Packages; [ mypy ];
          src = builtins.path {
            path = ./.;
            name = "pagewielder-py-src";
          };
          preConfigure = "./build.sh generate -g ${self.shortRev or self.dirtyShortRev}";
          patchPhase = "patchShebangs build.sh";
          checkPhase = "./build.sh check";
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
