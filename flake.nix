{
  description = "A tool for manipulating PDFs";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    flake-compat = {
      url = "github:edolstra/flake-compat";
      flake = false;
    };
    git-hooks = {
      url = "github:cachix/git-hooks.nix";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.flake-compat.follows = "flake-compat";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      git-hooks,
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
        pagewielder = makePagewielder pkgs;
        # The type checkers and pylint need to import pikepdf, which the hooks' own tools don't see otherwise.
        lintEnv = pkgs.python3.withPackages (
          ps:
          pagewielder.dependencies
          ++ [
            ps.mypy
            ps.pylint
          ]
        );
        preCommit = git-hooks.lib.${system}.run {
          src = ./.;
          hooks = {
            nixfmt.enable = true;
            black.enable = true;
            isort.enable = true;
            flake8.enable = true;
            pylint = {
              enable = true;
              package = lintEnv;
              # `./run.py lint` never pointed pylint at run.py itself, which doesn't meet its docstring rules.
              excludes = [ "^run\\.py$" ];
            };
            # The type checkers look at the whole project, not just the files that changed, so that a change to one
            # module is checked against the modules using it. Both take what to check from pyproject.toml.
            mypy = {
              enable = true;
              pass_filenames = false;
              package = lintEnv;
            };
            pyright = {
              enable = true;
              pass_filenames = false;
              entry = "${pkgs.pyright}/bin/pyright --pythonpath ${lintEnv}/bin/python";
            };
          };
        };
      in
      {
        packages.pagewielder = pagewielder;
        packages.default = self.packages.${system}.pagewielder;

        # Runs the hooks over the whole repo. They are kept out of the package's check phase so that a new release of
        # one of the tools fails `nix flake check` rather than the build.
        checks.pre-commit = preCommit;

        # The shellHook generates .pre-commit-config.yaml and installs the git hook.
        devShells.default = pkgs.mkShellNoCC {
          inputsFrom = [ pagewielder ];
          packages = preCommit.enabledPackages ++ [ pkgs.pre-commit ];
          inherit (preCommit) shellHook;
        };
      }
    );
}
