import argparse
import json
import subprocess


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    args = parser.parse_args()
    metadata = json.loads(subprocess.check_output(["docker", "image", "inspect", args.tag]))[0]
    tag = metadata["RepoTags"][0]
    if tag.count("/") == 0:
        tag = "docker.io/library/" + tag
    elif "." not in tag.split("/")[0] and ":" not in tag.split("/")[0]:
        tag = "docker.io/" + tag
    reference = tag.rsplit(":", 1)[0] + "@" + metadata["Id"]
    subprocess.run(
        [
            "docker",
            "exec",
            "fakery-control-plane",
            "ctr",
            "-n",
            "k8s.io",
            "images",
            "tag",
            "--force",
            tag,
            reference,
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
