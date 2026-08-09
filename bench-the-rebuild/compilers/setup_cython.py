from setuptools import setup
from Cython.Build import cythonize

if __name__ == "__main__":
    setup(
        name="ams-kernel-cython",
        ext_modules=cythonize(
            [
                "rebuild/pipeline/model.py",
                "rebuild/pipeline/specificity.py",
                "rebuild/pipeline/settle.py",
                "rebuild/pipeline/table.py",
            ],
            language_level=3,
            annotate=False,
            compiler_directives={
                "annotation_typing": True,
                "boundscheck": False,
                "wraparound": True,
                "initializedcheck": False,
                "cdivision": True,
                "infer_types": True,
            },
            nthreads=4,
        ),
        script_args=["build_ext", "--inplace"],
    )
