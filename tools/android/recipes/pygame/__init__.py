"""pygame 2.6.1 for python-for-android.

p4a ships a recipe for pygame **2.1.0**, but the client needs pygame >= 2.5
(``requirements.txt``) and pygame 2.1 never supported Python 3.12.  The recipe
is a straight copy of p4a's upstream ``pygame`` recipe with the version bumped:
the SDL2 configure/link dance in ``prebuild_arch`` is written against pygame's
own ``buildconfig/Setup.Android.SDL2.in``, which 2.6.1 still ships (only the
version line differs), so nothing else has to change.

Why a local recipe instead of patching the client down to 2.1: the client is
written against the 2.6 API (``FINGER*`` events, ``Surface`` flags), and
downgrading the game to satisfy a build tool is the kind of change that
silently changes behaviour.  The version pin belongs in the packaging layer.
"""
from os.path import join

from pythonforandroid.recipe import CompiledComponentsPythonRecipe
from pythonforandroid.toolchain import current_directory


class Pygame2Recipe(CompiledComponentsPythonRecipe):
    """SDL2-based pygame built against p4a's SDL2 bootstrap."""

    version = "2.6.1"
    # PyPI sdist, not the GitHub tag tarball: the sdist ships the generated C
    # (``src_c/_sdl2/audio.c``, ``src_c/pypm.c``, ...), the tag does not.  p4a
    # runs ``setup.py`` directly and never installs build requirements from
    # ``pyproject.toml``, so without those files the build would stop and ask
    # for Cython inside the target interpreter.
    url = ("https://files.pythonhosted.org/packages/49/cc/08bba60f00541f62aaa252"
           "ce0cfbd60aebd04616c0b9574f755b583e45ae/pygame-2.6.1.tar.gz")

    site_packages_name = "pygame"
    name = "pygame"

    depends = ["sdl2", "sdl2_image", "sdl2_mixer", "sdl2_ttf", "setuptools",
               "jpeg", "png"]
    call_hostpython_via_targetpython = False  # Due to setuptools
    install_in_hostpython = False

    def prebuild_arch(self, arch):
        super().prebuild_arch(arch)
        with current_directory(self.get_build_dir(arch.arch)):
            setup_template = open(join("buildconfig", "Setup.Android.SDL2.in")).read()
            env = self.get_recipe_env(arch)
            env["ANDROID_ROOT"] = join(self.ctx.ndk.sysroot, "usr")

            png = self.get_recipe("png", self.ctx)
            png_lib_dir = join(png.get_build_dir(arch.arch), ".libs")
            png_inc_dir = png.get_build_dir(arch)

            jpeg = self.get_recipe("jpeg", self.ctx)
            jpeg_inc_dir = jpeg_lib_dir = jpeg.get_build_dir(arch.arch)

            sdl_mixer_includes = ""
            sdl2_mixer_recipe = self.get_recipe("sdl2_mixer", self.ctx)
            for include_dir in sdl2_mixer_recipe.get_include_dirs(arch):
                sdl_mixer_includes += f"-I{include_dir} "

            sdl2_image_includes = ""
            sdl2_image_recipe = self.get_recipe("sdl2_image", self.ctx)
            for include_dir in sdl2_image_recipe.get_include_dirs(arch):
                sdl2_image_includes += f"-I{include_dir} "

            setup_file = setup_template.format(
                sdl_includes=(
                    " -I" + join(self.ctx.bootstrap.build_dir, "jni", "SDL", "include") +
                    " -L" + join(self.ctx.bootstrap.build_dir, "libs", str(arch)) +
                    " -L" + png_lib_dir + " -L" + jpeg_lib_dir +
                    " -L" + arch.ndk_lib_dir_versioned),
                sdl_ttf_includes="-I" + join(self.ctx.bootstrap.build_dir, "jni", "SDL2_ttf"),
                sdl_image_includes=sdl2_image_includes,
                sdl_mixer_includes=sdl_mixer_includes,
                jpeg_includes="-I" + jpeg_inc_dir,
                png_includes="-I" + png_inc_dir,
                freetype_includes="",
            )
            open("Setup", "w").write(setup_file)

    def get_recipe_env(self, arch):
        env = super().get_recipe_env(arch)
        env["USE_SDL2"] = "1"
        env["PYGAME_CROSS_COMPILE"] = "TRUE"
        env["PYGAME_ANDROID"] = "TRUE"
        return env


recipe = Pygame2Recipe()
